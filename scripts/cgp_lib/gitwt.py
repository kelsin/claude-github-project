"""Per-story git worktrees: create, sync (rebase), guard, remove."""
import fnmatch
import os
import subprocess
import sys
import time
from .util import covers, die, norm_path, out
from .store import cfg, load_data, update_data
from .board import issue_item, parse_pr_ref
from .gitutil import default_ref, fetch, git, resolve_repo_path, wt_path
from .repoconf import merged_globs
from .pr import allow_head, is_approved_head, pr_view


def is_dirty(wt):
    """True when the worktree has uncommitted work. (Unpushed commits are not checked: after a squash merge they never look merged.)"""
    p = subprocess.run(["git", "-C", wt, "status", "--porcelain"], capture_output=True, text=True)
    return p.returncode != 0 or bool(p.stdout.strip())


def cleanup_worktree(c, it):
    try:
        wt = wt_path(it)
        if os.path.isdir(wt) and not is_dirty(wt):
            base = resolve_repo_path(c, it["issueRepo"])
            git(base, "worktree", "remove", "--force", wt, check=False)
            git(base, "branch", "-D", f"cgp/{it['number']}", check=False)
    except SystemExit:
        pass


def valid_worktree(base, wt):
    """A directory that is a checkout git knows as a worktree of this clone (realpath on both sides: /private/var is /var on macOS)."""
    if subprocess.run(["git", "-C", wt, "rev-parse", "--git-dir"], capture_output=True).returncode:
        return False
    listed = subprocess.run(["git", "-C", base, "worktree", "list", "--porcelain"], capture_output=True, text=True).stdout
    return os.path.realpath(wt) in {os.path.realpath(line[len("worktree "):]) for line in listed.splitlines() if line.startswith("worktree ")}


def git_running():
    """Whether any git process runs on this machine (when that cannot be told, yes: a lock is then never taken for stale)."""
    try:
        return subprocess.run(["pgrep", "-x", "git"], capture_output=True).returncode == 0
    except OSError:
        return True


def clear_stale_index_lock(path, minutes=5):
    """Remove the repo's index.lock a crashed git left behind: only when it is older than `minutes` and no git process runs."""
    lock = git(path, "rev-parse", "--git-path", "index.lock", check=False)
    lock = lock if os.path.isabs(lock) else os.path.join(path, lock)
    if lock and os.path.exists(lock) and time.time() - os.path.getmtime(lock) > minutes * 60 and not git_running():
        os.remove(lock)


def repair_worktree(base, wt):
    """The story's worktree directory exists but git does not know it (a crash between mkdir and `worktree add`, or a registration
    pruned): an empty directory is removed, one with files is moved aside as <wt>.broken-<time> (nothing is deleted), then git forgets it."""
    if os.listdir(wt):
        os.rename(wt, f"{wt}.broken-{time.strftime('%Y%m%dT%H%M%S')}")
    else:
        os.rmdir(wt)
    git(base, "worktree", "prune")


def cmd_worktree(a):
    c = cfg()
    it = issue_item(c, a.item)
    repo = it["issueRepo"]
    base = resolve_repo_path(c, repo)
    branch = f"cgp/{it['number']}"
    wt = wt_path(it)
    fetch(base)
    git(base, "worktree", "prune")
    clear_stale_index_lock(base)
    default = default_ref(base)
    if os.path.isdir(wt):
        if valid_worktree(base, wt):
            clear_stale_index_lock(wt)
            out({"path": wt, "branch": git(wt, "branch", "--show-current"), "base": default, "repo": repo})
            return
        repair_worktree(base, wt)
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
    old_head = git(wt, "rev-parse", "HEAD")  # before anything moves it: only a rebase of an approved commit is itself approved
    remote = f"origin/{branch}"
    if git(wt, "rev-parse", "--verify", remote, check=False):
        # commits on the remote that are not (patch-equivalent to) ours: a local rebase not yet pushed is not someone else's push
        ahead_of_us = int(git(wt, "rev-list", "--count", "--cherry-pick", "--right-only", f"HEAD...{remote}") or 0)
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
        if not ahead_of_us and a.item not in load_data().get("tainted", []) and is_approved_head(a.item, old_head):
            allow_head(a.item, git(wt, "rev-parse", "HEAD"))  # a clean rebase of the reviewed commit: merge needs no new approval
    out({"state": "rebased" if moved else "clean", "behind": behind, "base": default})


def cmd_guard(a):
    """Refuse changes to CI/permission files that the approved plan does not list."""
    c = cfg()
    it = issue_item(c, a.item)
    wt = wt_path(it)
    if not os.path.isdir(wt):
        die("no worktree for this story")
    names = [n for n in git(wt, "diff", "-z", "--name-only", "--no-renames", f"{default_ref(wt)}...HEAD").split("\0") if n]  # NUL-split: no quoting
    d = load_data()
    snap = d.get("approvedTouches", {}).get(a.item)  # what was declared when the story was approved; a story from before that
    allowed = [norm_path(t) for t in (snap if snap is not None else d.get("touches", {}).get(a.item, []))]  # existed uses its touches
    if it["skipPlan"] and a.item not in d.get("parents", {}):
        allowed = []  # no plan was approved, so nothing guarded was (a sub-story's files were: they are in its parent's approved split)
    guarded = [g.lower() for g in merged_globs(c, "guardFiles", [it["issueRepo"]])]
    bad = [n for n in names if any(fnmatch.fnmatchcase(x.lower(), g) for g in guarded for x in (n, os.path.basename(n)))
           and not any(covers(t, n) for t in allowed)]
    out({"ok": not bad, "violations": bad})
    if bad:
        sys.exit(4)


def unsaved_work(c, it, base, wt):
    """Why removing the worktree would lose work (None when nothing would be): uncommitted changes, or commits origin/<branch> lacks.
    A branch that is not on the remote is fine only once the story's PR is merged (the branch was deleted after the squash)."""
    status = subprocess.run(["git", "-C", wt, "status", "--porcelain"], capture_output=True, text=True)
    if status.returncode:
        return "git cannot read its state"
    if status.stdout.strip():
        return "it has uncommitted changes"
    ref = parse_pr_ref(c, it["pr"])
    if ref and (pr_view(*ref, check=False) or {}).get("state") == "MERGED":
        return None
    try:
        fetch(base)
    except SystemExit:
        return "the remote cannot be fetched to check for unpushed commits"
    remote = f"origin/cgp/{it['number']}"
    if subprocess.run(["git", "-C", wt, "rev-parse", "--verify", remote], capture_output=True).returncode:
        return f"{remote} does not exist, so its commits were never pushed (and its PR is not merged)"
    ahead = subprocess.run(["git", "-C", wt, "rev-list", "--count", f"{remote}..HEAD"], capture_output=True, text=True)
    if ahead.returncode or ahead.stdout.strip() != "0":
        return f"it has commits that are not on {remote}"
    return None


def cmd_worktree_remove(a):
    c = cfg()
    it = issue_item(c, a.item)
    wt = wt_path(it)
    if os.path.isdir(wt):
        base = resolve_repo_path(c, it["issueRepo"])
        problem = None if a.discard else unsaved_work(c, it, base, wt)
        if problem:
            die(f"not removing {wt}: {problem}. Push or commit it first; to throw it away: cgp worktree-remove {a.item} --discard (not for agents)")
        git(base, "worktree", "remove", "--force", wt)
        git(base, "branch", "-D", f"cgp/{it['number']}", check=False)
    out({"removed": wt})
