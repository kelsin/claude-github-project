"""Per-story git worktrees: create, sync (rebase), guard, remove."""
import fnmatch
import os
import subprocess
import sys
from .util import covers, die, norm_path, out
from .store import cfg, load_data, update_data
from .board import issue_item
from .gitutil import default_ref, fetch, git, resolve_repo_path, wt_path
from .repoconf import merged_globs
from .pr import allow_head


def cleanup_worktree(c, it):
    try:
        wt = wt_path(it)
        if os.path.isdir(wt):
            base = resolve_repo_path(c, it["issueRepo"])
            git(base, "worktree", "remove", "--force", wt, check=False)
            git(base, "branch", "-D", f"cgp/{it['number']}", check=False)
    except SystemExit:
        pass


def cmd_worktree(a):
    c = cfg()
    it = issue_item(c, a.item)
    repo = it["issueRepo"]
    base = resolve_repo_path(c, repo)
    branch = f"cgp/{it['number']}"
    wt = wt_path(it)
    fetch(base)
    git(base, "worktree", "prune")
    default = default_ref(base)
    if os.path.isdir(wt):
        out({"path": wt, "branch": git(wt, "branch", "--show-current"), "base": default, "repo": repo})
        return
    os.makedirs(os.path.dirname(wt), exist_ok=True)
    has_local = git(base, "rev-parse", "--verify", f"refs/heads/{branch}", check=False)
    has_remote = git(base, "rev-parse", "--verify", f"origin/{branch}", check=False)
    # only fast-forward the local branch to the remote one; local commits that were never pushed must survive
    behind_only = subprocess.run(["git", "-C", base, "merge-base", "--is-ancestor", f"refs/heads/{branch}", f"origin/{branch}"],
                                 capture_output=True).returncode == 0 if has_local and has_remote else True
    if has_remote and behind_only:
        git(base, "worktree", "add", "-B", branch, wt, f"origin/{branch}")
    elif has_local:
        git(base, "worktree", "add", wt, branch)
    else:
        git(base, "worktree", "add", "-b", branch, wt, default)
    out({"path": wt, "branch": branch, "base": default, "repo": repo})


def rebase_in_progress(wt):
    return any(os.path.isdir(os.path.join(wt, git(wt, "rev-parse", "--git-path", d)))
               for d in ("rebase-merge", "rebase-apply"))


def run_rebase(wt, target):
    """None on success, else a result dict: conflict (rebase left in progress) or error."""
    p = subprocess.run(["git", "-C", wt, "rebase", "--autostash", target], capture_output=True, text=True)
    if p.returncode == 0:
        return None
    if rebase_in_progress(wt):
        return {"state": "conflict", "files": git(wt, "diff", "--name-only", "--diff-filter=U", check=False).splitlines()}
    return {"state": "error", "stderr": p.stderr.strip()}


def taint(item):
    """A conflict was resolved by hand: later clean rebases may no longer be passed off as already approved (see pr.allow_head)."""
    update_data(lambda d: d.setdefault("tainted", []).append(item) if item not in d.get("tainted", []) else None)


def cmd_sync(a):
    """Bring the story's worktree up to date: first onto any commits others pushed to its own branch, then
    onto the freshly fetched default branch.

    clean: nothing to do; rebased: moved (push with --force-with-lease if the branch is on the remote);
    conflict: rebase left in progress, `files` lists unresolved paths: fix, git add, then
    `GIT_EDITOR=true git rebase --continue`, and run sync again; error: git refused (see `stderr`).
    """
    c = cfg()
    it = issue_item(c, a.item)
    wt = wt_path(it)
    if not os.path.isdir(wt):
        die("no worktree for this story; run: cgp worktree <item>")
    fetch(wt)
    default = default_ref(wt)
    if rebase_in_progress(wt):
        out({"state": "conflict", "base": default, "note": "an earlier rebase is still in progress",
             "files": git(wt, "diff", "--name-only", "--diff-filter=U", check=False).splitlines()})
        return
    branch = git(wt, "branch", "--show-current")
    if not branch:
        die("worktree is on a detached HEAD; check out the story branch first")
    moved = ahead_of_us = 0
    remote = f"origin/{branch}"
    if git(wt, "rev-parse", "--verify", remote, check=False):
        ahead_of_us = int(git(wt, "rev-list", "--count", f"HEAD..{remote}") or 0)
        if ahead_of_us:  # someone (e.g. a GitHub suggested change) pushed to the PR branch: keep their commits
            res = run_rebase(wt, remote)
            if res:
                taint(a.item)
                out({**res, "base": remote})
                return
            moved += ahead_of_us
    behind = int(git(wt, "rev-list", "--count", f"HEAD..{default}") or 0)
    if behind:
        res = run_rebase(wt, default)
        if res:
            if res["state"] == "conflict":
                taint(a.item)
            out({**res, "behind": behind, "base": default})
            return
        moved += behind
        if not ahead_of_us and a.item not in load_data().get("tainted", []):
            allow_head(a.item, git(wt, "rev-parse", "HEAD"))  # a clean rebase of our own commits: merge needs no new approval
    out({"state": "rebased" if moved else "clean", "behind": behind, "base": default})


def cmd_guard(a):
    """Refuse changes to CI/permission files that the approved plan does not list."""
    c = cfg()
    it = issue_item(c, a.item)
    wt = wt_path(it)
    if not os.path.isdir(wt):
        die("no worktree for this story")
    names = git(wt, "diff", "--name-only", "--no-renames", f"{default_ref(wt)}...HEAD").splitlines()
    allowed = [norm_path(t) for t in load_data().get("touches", {}).get(a.item, [])]
    guarded = merged_globs(c, "guardFiles", [it["issueRepo"]])
    bad = [n for n in names if any(fnmatch.fnmatchcase(x, g) for g in guarded for x in (n, os.path.basename(n)))
           and not any(covers(t, n) for t in allowed)]
    out({"ok": not bad, "violations": bad})
    if bad:
        sys.exit(4)


def cmd_worktree_remove(a):
    c = cfg()
    it = issue_item(c, a.item)
    wt = wt_path(it)
    if os.path.isdir(wt):
        base = resolve_repo_path(c, it["issueRepo"])
        git(base, "worktree", "remove", "--force", wt)
        git(base, "branch", "-D", f"cgp/{it['number']}", check=False)
    out({"removed": wt})
