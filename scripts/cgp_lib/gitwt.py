"""Per-story git worktrees: create, sync (rebase), guard, remove."""
import contextlib
import fnmatch
import io
import os
import subprocess
import sys
import time
from .util import IS_WINDOWS, covers, die, norm_path, out
from .store import cfg, load_data, update_data
from .board import issue_item, known_repo, parse_pr_ref
from .gitutil import default_ref, fetch, git, resolve_repo_path, wt_path
from .repoconf import merged_globs
from .pr import allow_head, is_approved_head, pr_view


def cleanup_worktree(c, it, fetched=None):
    """Remove a finished story's worktree and branch unless that would lose work (see unsaved_work). None when it is gone,
    else why it was kept. `fetched` (repo clone -> fetch worked) lets one sweep fetch each repo once. Never prints, never raises."""
    wt = wt_path(it)
    if not os.path.isdir(wt):
        return None
    try:
        with contextlib.redirect_stderr(io.StringIO()):  # die() prints before it exits
            base = resolve_repo_path(c, known_repo(c, it["issueRepo"]) or it["issueRepo"])
            problem = unsaved_work(c, it, base, wt, fetched)
            if not problem:
                git(base, "worktree", "remove", "--force", wt, check=False)
                if os.path.isdir(wt):  # e.g. a locked worktree: git refused, so the branch stays too
                    return "git worktree remove failed"
                git(base, "branch", "-D", f"cgp/{it['number']}", check=False)
    except SystemExit:
        return "git could not be run for it"
    return problem


def valid_worktree(base, wt):
    """A directory that is a checkout git knows as a worktree of this clone (realpath on both sides: /private/var is /var on macOS)."""
    if subprocess.run(["git", "-C", wt, "rev-parse", "--git-dir"], capture_output=True).returncode:
        return False
    listed = subprocess.run(["git", "-C", base, "worktree", "list", "--porcelain"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return os.path.realpath(wt) in {os.path.realpath(line[len("worktree "):]) for line in listed.splitlines() if line.startswith("worktree ")}


def git_running():
    """Whether any git process runs on this machine (when that cannot be told, yes: a lock is then never taken for stale)."""
    if IS_WINDOWS:
        return True
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
        try:
            os.rename(wt, f"{wt}.broken-{time.strftime('%Y%m%dT%H%M%S')}")
        except OSError as e:
            die(f"could not move the broken worktree {wt} aside ({e}); close anything using it (editor, terminal) and retry")
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
    p = subprocess.run(["git", "-C", wt, "rebase", "--autostash", target], capture_output=True, text=True, encoding="utf-8", errors="replace")
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


def pr_view_of(c, it):
    ref = parse_pr_ref(c, it["pr"])
    return (pr_view(*ref, check=False) or {}) if ref else {}


def dirty_summary(wt):
    """Why `git status` is not clean, naming the files ("2 modified, 1 untracked files: a, b, c"); None when it is clean."""
    status = subprocess.run(["git", "-C", wt, "status", "--porcelain"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if status.returncode:
        return "git cannot read its state"
    lines = [line for line in status.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    untracked = [line[3:] for line in lines if line.startswith("??")]
    modified = [line[3:] for line in lines if not line.startswith("??")]
    names = [*modified, *untracked]
    more = f", and {len(names) - 5} more" if len(names) > 5 else ""
    return f"{len(modified)} modified, {len(untracked)} untracked files: {', '.join(names[:5])}{more}"


def merged_contained(wt, it, view):
    """The story's PR is merged and everything committed here is inside the PR's head: the local branch, and HEAD too (a detached HEAD, or
    a stopped rebase, may hold commits the branch ref does not). A head object this clone lacks, an unreachable GitHub or anything
    unclear is not contained."""
    oid = view.get("headRefOid")
    if view.get("state") != "MERGED" or not oid:
        return False

    def git_ok(*args):
        p = subprocess.run(["git", "-C", wt, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
        return p.stdout.strip() if p.returncode == 0 else None
    if git_ok("merge-base", "--is-ancestor", f"refs/heads/cgp/{it['number']}", oid) is None:
        return False
    try:
        if not rebase_in_progress(wt):
            return git_ok("merge-base", "--is-ancestor", "HEAD", oid) is not None
        merge_dir = os.path.join(wt, git(wt, "rev-parse", "--git-path", "rebase-merge"))
        if not os.path.isdir(merge_dir):  # rebase-apply: no record of what was replayed
            return False
        with open(os.path.join(merge_dir, "onto"), encoding="utf-8") as f:
            onto = f.read().strip()
        with open(os.path.join(merge_dir, "done"), encoding="utf-8") as f:
            picks = sum(1 for line in f if line.split(" ", 1)[0] in ("pick", "p", "reword", "r", "edit", "e", "squash", "s", "fixup", "f"))
        ahead = git_ok("rev-list", "--count", f"{onto}..HEAD")
        return ahead is not None and int(ahead) <= picks
    except (OSError, SystemExit, ValueError):
        return False


def uncommitted_work(wt):
    """Why the worktree holds work that only exists there (None when it does not): a rebase left in progress (--autostash hides
    changes from `git status` then), or a dirty tree. Read-only."""
    if rebase_in_progress(wt):
        return "a rebase is still in progress"
    summary = dirty_summary(wt)
    if summary and summary.startswith("git cannot"):
        return summary
    return f"it has uncommitted changes ({summary})" if summary else None


def unsaved_work(c, it, base, wt, fetched=None):
    """Why removing the worktree would lose work (None when nothing would be): uncommitted changes, a rebase in progress, or commits origin/<branch> lacks.
    None at once when the story's PR is merged and the local branch is inside the PR's head (merged_contained).
    A branch that is not on the remote is fine only once the story's PR is merged (the branch was deleted after the squash) and
    HEAD is that PR's head, or when HEAD is already on the default branch. Local checks run before anything is fetched; an unknown
    default branch or a failed fetch keeps the worktree. `fetched` caches fetches per clone."""
    view = pr_view_of(c, it)
    if merged_contained(wt, it, view):  # what is left (rebase debris, regenerated files) is not worth keeping
        return None
    merged = view.get("state") == "MERGED"
    problem = uncommitted_work(wt)
    if problem:
        return problem
    def rev(name):
        return subprocess.run(["git", "-C", wt, "rev-parse", "--verify", "-q", name], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    head = rev("HEAD")
    branch_tip = rev(f"refs/heads/cgp/{it['number']}")  # `branch -D` follows removal: a branch HEAD has left must be safe too
    tips = [head] + ([branch_tip] if branch_tip and branch_tip != head else [])
    try:
        default = default_ref(base)
    except SystemExit:
        default = None
    ok = None

    def tip_problem(tip):
        nonlocal ok
        if merged:
            return None if tip and tip == view.get("headRefOid") else "HEAD is not the head of its merged PR (commits were added after)"
        if not default:
            return "the default branch is unknown"
        ahead_of_default = subprocess.run(["git", "-C", wt, "rev-list", "--count", f"{default}..{tip}"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if ahead_of_default.returncode == 0 and ahead_of_default.stdout.strip() == "0":
            return None  # nothing on it that the default branch lacks
        if ok is None:
            if fetched is None or base not in fetched:
                try:
                    fetch(base)
                    ok = True
                except SystemExit:
                    ok = False
                if fetched is not None:
                    fetched[base] = ok
            else:
                ok = fetched[base]
        if not ok:
            return "the remote cannot be fetched to check for unpushed commits"
        remote = f"origin/cgp/{it['number']}"
        if subprocess.run(["git", "-C", wt, "rev-parse", "--verify", remote], capture_output=True).returncode:
            return f"{remote} does not exist, so its commits were never pushed (and its PR is not merged)"
        ahead = subprocess.run(["git", "-C", wt, "rev-list", "--count", f"{remote}..{tip}"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if ahead.returncode or ahead.stdout.strip() != "0":
            return f"it has commits that are not on {remote}"
        return None

    return next((p for p in map(tip_problem, tips) if p), None)


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
