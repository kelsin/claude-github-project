"""Wrappers around the gh CLI (GraphQL, paginated REST) and the comment trust model."""
import json
import subprocess
import sys
from .consts import MARK
from .util import die


def gh(*args, input=None, check=True):
    p = subprocess.run(["gh", *args], input=input, capture_output=True, text=True)
    if check and p.returncode:
        die(f"gh {' '.join(args[:4])} failed: {(p.stderr or p.stdout).strip()}")
    return p


def gql(query, **variables):
    body = json.dumps({"query": query, "variables": variables})
    p = gh("api", "graphql", "--input", "-", input=body, check=False)  # exits 1 on any error, even with usable data
    try:
        res = json.loads(p.stdout)
    except json.JSONDecodeError:
        die(f"gh api graphql failed: {(p.stderr or p.stdout).strip()}")
    data, errors = res.get("data"), res.get("errors")
    if data is None or (any(v is None for v in data.values()) and (errors or query.lstrip().startswith("mutation"))):
        die(f"graphql: {json.dumps(errors or res)}")
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
    p = gh("api", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}")
    return json.loads(p.stdout)
