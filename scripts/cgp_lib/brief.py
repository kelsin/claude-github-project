"""A cached one-page map of a repo for workers, kept per repo under the cgp home and keyed by the default-branch commit it was
written at. The CLI never writes the text itself: a worker sub-agent does and pipes it in (`cgp brief <repo> --write --sha <sha>`)."""
import os
import subprocess
import time
from .consts import DEFAULTS, HOME
from .util import die, out
from .store import cfg, load_json, locked, save_json
from .board import get_item, require_repo
from .gitutil import default_ref, git, resolve_repo_path

BRIEF_CAP = 6000  # characters, so `cgp prepare` output stays small
CUT = "\n[cut]"


def brief_path(repo):
    return os.path.join(HOME, "briefs", *repo.split("/")) + ".json"


def is_ancestor(path, a, b):
    return subprocess.run(["git", "-C", path, "merge-base", "--is-ancestor", a, b], capture_output=True).returncode == 0


def brief_report(c, repo):
    """Status of the repo's brief: fresh (written at the default branch head), behind (default branch moved fewer than briefThreshold
    changed files), stale (moved further, or the stored commit is gone from its history), missing, or unavailable (no clone or
    no default branch). Only fresh and behind carry the text; `generate` says a worker should write a new one."""
    res = {"repo": repo, "threshold": c["settings"].get("briefThreshold", DEFAULTS["briefThreshold"])}
    path = c["repos"].get(repo)
    if not path or not os.path.isdir(path):
        return {**res, "status": "unavailable", "generate": False}
    try:
        ref = default_ref(path)
    except SystemExit:
        return {**res, "status": "unavailable", "generate": False}
    head = git(path, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", check=False)
    if not head:
        return {**res, "status": "unavailable", "generate": False}
    res.update(head=head, clone=path)
    stored = load_json(brief_path(repo), None)
    if not isinstance(stored, dict) or not stored.get("text") or not stored.get("sha"):
        return {**res, "status": "missing", "generate": True}
    res["sha"] = stored["sha"]
    if stored["sha"] == head:
        return {**res, "status": "fresh", "changedFiles": 0, "generate": False, "brief": stored["text"]}
    if not is_ancestor(path, stored["sha"], ref):  # rewritten away by a force push, or not in this clone
        return {**res, "status": "stale", "generate": True}
    res["changedFiles"] = len(git(path, "diff", "--name-only", stored["sha"], head, check=False).splitlines())
    if res["changedFiles"] >= max(res["threshold"], 1):
        return {**res, "status": "stale", "generate": True}
    return {**res, "status": "behind", "generate": False, "brief": stored["text"]}


def write_brief(c, repo, sha):
    from .story import read_body  # story imports this module
    text = read_body("empty brief (pipe it on stdin)")
    if sha.startswith("-"):
        die("--sha must be a commit id")
    path = resolve_repo_path(c, repo)
    full = git(path, "rev-parse", "--verify", "--end-of-options", f"{sha}^{{commit}}", check=False)
    if not full:
        die(f"{sha} is not a commit in the clone of {repo}")
    ref = default_ref(path)
    if not is_ancestor(path, full, ref):
        die(f"{sha} is not on the default branch ({ref})")
    if len(text) > BRIEF_CAP:
        text = text[:BRIEF_CAP] + CUT
    with locked():  # two workers writing at once: the older commit must not replace the newer
        old = (load_json(brief_path(repo), None) or {}).get("sha")
        if old and old != full and is_ancestor(path, full, old) and is_ancestor(path, old, ref):
            die(f"the stored brief was written at a newer commit ({old[:10]}); not replacing it with {full[:10]}")
        os.makedirs(os.path.dirname(brief_path(repo)), exist_ok=True)
        save_json(brief_path(repo), {"sha": full, "writtenAt": int(time.time()), "text": text})
    return {"repo": repo, "sha": full, "written": len(text)}


def cmd_brief(a):
    c = cfg()
    repo = require_repo(c, a.target) if "/" in a.target else get_item(c, a.target)["issueRepo"]
    if not repo:
        die("that story has no repo yet")
    if a.write:
        if not a.sha:
            die("usage: cgp brief <repo> --write --sha <default branch commit> (text on stdin)")
        out(write_brief(c, repo, a.sha))
        return
    out(brief_report(c, repo))
