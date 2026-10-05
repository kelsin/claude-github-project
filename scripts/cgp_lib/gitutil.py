"""Plain git helpers shared by the worktree and repo-config modules."""
import os
import re
import subprocess
import time
from .consts import HOME
from .util import die


def resolve_repo_path(c, repo):
    path = c["repos"].get(repo)
    if not path or not os.path.isdir(path):
        die(f"no local clone known for {repo}; run: cgp repo-path {repo} <path>")
    return path


_cwd_repo = []


def cwd_repo():
    """'owner/name' of the GitHub repo the current directory belongs to (its `origin` remote; linked worktrees share it), else None."""
    if not _cwd_repo:
        try:
            url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
        except OSError:  # no git, or the directory is gone
            url = ""
        m = re.search(r"(?:^|[@/])github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
        _cwd_repo.append(m.group(1) if m else None)
    return _cwd_repo[0]


def git(path, *args, check=True):
    p = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    if check and p.returncode:
        die(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout.strip()


def wt_path(it):
    owner, name = it["issueRepo"].split("/")
    return os.path.join(HOME, "worktrees", owner, name, str(it["number"]))


def fetch(path):
    for _ in range(3):  # concurrent workers fetching one repo can briefly lock each other out
        p = subprocess.run(["git", "-C", path, "fetch", "origin", "--prune"], capture_output=True, text=True)
        if p.returncode == 0:
            return
        time.sleep(2)
    die(f"git fetch failed: {p.stderr.strip()}")


def default_ref(path):
    sym = lambda: git(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    ref = sym()
    if not ref:
        git(path, "remote", "set-head", "origin", "--auto", check=False)
        ref = sym()
    if not ref:
        die("cannot determine the default branch (origin/HEAD unset and remote unreachable)")
    return ref
