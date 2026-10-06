"""Wrappers around the gh CLI (GraphQL, paginated REST) and the comment trust model."""
import json
import os
import re
import subprocess
import sys
import time
from .consts import MARK
from .util import die


class RateLimited(SystemExit):
    """GitHub's rate limit. A SystemExit like die() raises (code 1), so every caller that survives a failed gh call survives this
    too; the daemon alone looks for it, to wait longer."""


TRANSIENT = re.compile(r"HTTP 5\d\d|time(d )?out|\bEOF\b|connection (reset|refused|closed)|no such host|TLS handshake|temporary failure", re.I)
RATE_LIMIT = re.compile(r"rate limit|HTTP 429|RATE_LIMITED|abuse detection", re.I)
BACKOFF = tuple(float(x) for x in os.environ.get("CGP_GH_BACKOFF", "1,3").split(","))  # waits before the retries of a transient failure
WRITE_FLAGS = {"-f", "-F", "-X", "--method", "--input", "--field", "--raw-field"}
# GraphQL mutations that give the same result when sent twice; every other one (creating things) is never retried
IDEMPOTENT_MUTATIONS = ("updateProjectV2ItemFieldValue", "clearProjectV2ItemFieldValue", "updateProjectV2View", "updateProjectV2Field",
                        "linkProjectV2ToRepository", "addProjectV2ItemById")


def readonly(args):
    """Whether a gh call only reads, so repeating it is safe (anything that writes, comments included, is never retried)."""
    if args[:1] == ("pr",):
        return args[1:2] in (("view",), ("checks",), ("diff",), ("list",))
    return args[:1] == ("api",) and "graphql" not in args and not WRITE_FLAGS & set(args)


def fail(msg, text):
    if RATE_LIMIT.search(text or ""):
        print(f"cgp: {msg}", file=sys.stderr)
        raise RateLimited(1)
    die(msg)


def gh(*args, input=None, check=True, retry=None):
    retry = readonly(args) if retry is None else retry
    for delay in (*BACKOFF, None):
        p = subprocess.run(["gh", *args], input=input, capture_output=True, text=True)
        if not p.returncode or not retry or delay is None or not TRANSIENT.search(p.stderr + p.stdout):
            break
        time.sleep(delay)
    if check and p.returncode:
        text = (p.stderr or p.stdout).strip()
        fail(f"gh {' '.join(args[:4])} failed: {text}", p.stderr + p.stdout)
    return p


def gql(query, **variables):
    body = json.dumps({"query": query, "variables": variables})
    mutation = re.search(r"\{\s*(\w+)", query) if query.lstrip().startswith("mutation") else None
    p = gh("api", "graphql", "--input", "-", input=body, check=False,  # exits 1 on any error, even with usable data
           retry=not mutation or mutation.group(1) in IDEMPOTENT_MUTATIONS)
    try:
        res = json.loads(p.stdout)
    except json.JSONDecodeError:
        fail(f"gh api graphql failed: {(p.stderr or p.stdout).strip()}", p.stderr + p.stdout)
    data, errors = res.get("data"), res.get("errors")
    if data is None or (any(v is None for v in data.values()) and (errors or query.lstrip().startswith("mutation"))):
        fail(f"graphql: {json.dumps(errors or res)}", json.dumps(errors or res))
    if errors:  # partial result (e.g. one item whose content is not readable): keep the data, report the rest
        print(f"cgp: graphql: {json.dumps(errors)}", file=sys.stderr)
    return data


def rest(path, *extra):
    sep = "&" if "?" in path else "?"
    text = gh("api", "--paginate", f"{path}{sep}per_page=100", *extra).stdout
    dec, i, merged = json.JSONDecoder(), 0, []
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return merged
        chunk, i = dec.raw_decode(text, i)
        merged.extend(chunk if isinstance(chunk, list) else [chunk])


_cache = {}


def viewer():
    if "viewer" not in _cache:
        _cache["viewer"] = gh("api", "user", "-q", ".login").stdout.strip()
    return _cache["viewer"]


def is_bot(comment):
    return (comment.get("user") or {}).get("type") == "Bot"


def is_agent(comment):
    """An agent comment carries the marker AND was posted by this gh account (anyone can type the marker)."""
    return MARK[:8] in (comment.get("body") or "") and (comment.get("user") or {}).get("login") == viewer()


def trusted(repo, comment):
    """Only the gh account itself, repo owners and collaborators with write access may give instructions."""
    login = (comment.get("user") or {}).get("login")
    assoc = comment.get("author_association")
    if not login:
        return False
    if login == viewer() or assoc == "OWNER":
        return True
    if assoc not in ("MEMBER", "COLLABORATOR"):
        return False
    key = ("perm", repo, login)
    if key not in _cache:
        p = gh("api", f"repos/{repo}/collaborators/{login}/permission", check=False)
        try:
            _cache[key] = p.returncode == 0 and json.loads(p.stdout).get("permission") in ("admin", "maintain", "write")
        except json.JSONDecodeError:
            _cache[key] = False
    return _cache[key]


def post_comment(repo, number, body):
    p = gh("api", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}", retry=False)  # a retry could post it twice
    return json.loads(p.stdout)
