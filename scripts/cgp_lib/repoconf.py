"""Per-repo settings from a `.cgp.json` at the root of the repo's DEFAULT branch (never from a story's own branch: a story could
otherwise loosen its own guard). Unknown keys and wrong types are ignored."""
import json
import re
from .util import die, out
from .store import cfg
from .board import get_item, require_repo
from .gitutil import default_ref, git

FILE = ".cgp.json"
STR, LIST, MAP = "string", "list", "map"
KNOWN = {"test": STR, "lint": STR, "sharedFiles": LIST, "guardFiles": LIST, "reviewers": LIST, "preview": MAP}
_cache = {}


def clean(raw):
    """Keep the known keys that have the right type; strings are cut to 500 characters, lists to 50 short strings."""
    res = {}
    for key, kind in KNOWN.items():
        v = raw.get(key) if isinstance(raw, dict) else None
        if kind == STR and isinstance(v, str) and v.strip():
            res[key] = v.strip()[:500]
        elif kind == LIST and isinstance(v, list):
            res[key] = [x.strip()[:200] for x in v if isinstance(x, str) and x.strip()][:50]
        elif kind == MAP and isinstance(v, dict):
            res[key] = {k: x[:300] for k, x in v.items() if isinstance(x, str)}
    return res


def repo_config(c, repo):
    """The cleaned .cgp.json of a linked repo's default branch; {} when there is none, no clone, or it is not valid JSON."""
    if repo in _cache:
        return _cache[repo]
    cfg_ = {}
    path = c["repos"].get(repo)
    if path:
        try:
            text = git(path, "show", f"{default_ref(path)}:{FILE}", check=False)
            cfg_ = clean(json.loads(text)) if text else {}
        except (SystemExit, ValueError):
            cfg_ = {}
    _cache[repo] = cfg_
    return cfg_


def merged_globs(c, key, repos=()):
    """The board setting `key` plus the same key from each repo's .cgp.json (a repo can only add to a guard, never remove)."""
    seen = list(c["settings"].get(key, []))
    for r in repos:
        seen += [g for g in repo_config(c, r).get(key, []) if g not in seen]
    return seen


def cmd_repo_config(a):
    c = cfg()
    repo = require_repo(c, a.target) if "/" in a.target else get_item(c, a.target)["issueRepo"]
    if not repo:
        die("that story has no repo yet")
    out({"repo": repo, "file": FILE, "config": repo_config(c, repo)})


def safe_pattern(pattern, pr):
    """Compile a preview URL pattern from a repo config (`{pr}` becomes the PR number); None when it is not a valid regex."""
    try:
        return re.compile(pattern.replace("{pr}", str(pr)))
    except re.error:
        return None
