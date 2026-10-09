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
from .pr import allow_head, cancel_auto_merge, forget_review, is_approved_head, pr_view
from .gh import gh
from .stack import blocker_ref, pr_files


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
        stack = load_data().get("stack", {}).get(a.item)  # a stacked story is cut from the blocker commit it was recorded on
        git(base, "worktree", "add", "-b", branch, wt, stack["tip"] if stack else default)
        if stack:
            out({"path": wt, "branch": branch, "base": default, "repo": repo, "stackedOn": stack["branch"]})
            return
    out({"path": wt, "branch": branch, "base": default, "repo": repo})


def rebase_in_progress(wt):
    return any(os.path.isdir(os.path.join(wt, git(wt, "rev-parse", "--git-path", d)))
               for d in ("rebase-merge", "rebase-apply"))


def run_rebase(wt, target, upstream=None):
    """None on success, else a result dict: conflict (rebase left in progress) or error. With `upstream`, only the commits after it are
    replayed onto `target` (git rebase --onto)."""
    cmd = ["rebase", "--autostash", *(["--onto", target, upstream] if upstream else [target])]
    p = subprocess.run(["git", "-C", wt, *cmd], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode == 0:
        return None
    if rebase_in_progress(wt):
        return {"state": "conflict", "files": git(wt, "diff", "--name-only", "--diff-filter=U", check=False).splitlines()}
    return {"state": "error", "stderr": p.stderr.strip()}


def taint(item):
    """A conflict was resolved by hand: later clean rebases may no longer be passed off as already approved (see pr.allow_head)."""
    update_data(lambda d: d.setdefault("tainted", []).append(item) if item not in d.get("tainted", []) else None)


def is_ancestor(wt, commit, of="HEAD"):
    return subprocess.run(["git", "-C", wt, "merge-base", "--is-ancestor", commit, of], capture_output=True).returncode == 0


def stack_sync(c, it, wt, rec, default):
    """cmd_sync for a stacked story, in place of the rebase onto the default branch (see stack.py). While the blocker's PR is open the
    story is rebased onto the blocker's branch, from the commit it was built on (`tip`, which then moves forward). Once it is merged
    the story is rebased onto the default branch from that commit (the blocker was squash-merged, so its own commits must not be
    replayed), the PR is retargeted and only then the record, and with it the merge gate, goes away. A closed blocker, a recorded commit
    that is no longer in the branch and any conflict leave the record in place. Neither rebase is passed off as approved: the story is
    sent back for a new review. Returns the result to print."""
    from .story import send_back  # story imports this module
    item = it["item"]
    repo, num = blocker_ref(rec)
    subprocess.run(["git", "-C", wt, "fetch", "origin", f"refs/pull/{num}/head"], capture_output=True)  # the commits stay local once the branch is deleted
    view = pr_view(repo, num, check=False)
    if not view:
        die("could not read the blocker's PR; not changing the stacked story")
    if view["state"] == "CLOSED":
        send_back(c, item, f"The blocker's PR ({rec['pr']}) was closed without merging, so this story, stacked on {rec['branch']}, "
                           "cannot go on as it is. It is back in Implement; the user decides what happens to the stack.")
        return {"state": "blocker-closed", "pr": rec["pr"], "base": rec["branch"]}
    merged = view["state"] == "MERGED"
    target = default if merged else f"origin/{rec['branch']}"
    new_tip = git(wt, "rev-parse", "--verify", "-q", target, check=False)
    if not new_tip:
        return {"state": "error", "stderr": f"{target} does not exist", "base": target}
    behind = int(git(wt, "rev-list", "--count", f"HEAD..{target}") or 0)
    if not merged and new_tip == rec["tip"]:
        return {"state": "clean", "behind": 0, "base": target}
    if not (merged and rec.get("onto") and is_ancestor(wt, rec["onto"])):  # not rebased onto the default branch already (e.g. by hand after a conflict)
        if not is_ancestor(wt, rec["tip"]):
            taint(item)
            send_back(c, item, f"This story was recorded as built on {rec['branch']} at {rec['tip'][:8]}, but that commit is not in its "
                               "branch any more, so it cannot be rebased safely. It is back in Implement.")
            return {"state": "tainted", "base": target, "note": "the recorded blocker commit is not in the branch"}
        if merged:
            update_data(lambda d: d["stack"][item].__setitem__("onto", new_tip))
        res = run_rebase(wt, target, upstream=rec["tip"])
        if res:
            if res["state"] == "conflict":
                taint(item)
            return {**res, "behind": behind, "base": target}
    if not merged:
        try:
            files = pr_files(repo, num)
        except SystemExit:
            files = rec["files"]
        update_data(lambda d: d["stack"][item].update(tip=new_tip, files=files))
    else:
        retarget(c, it, default)
        update_data(lambda d: d.get("stack", {}).pop(item, None))
    if it["column"] in ("pr_review", "pr_approved"):
        send_back(c, item, f"This PR was rebased onto {'the default branch after its blocker merged' if merged else rec['branch']} and so changed after "
                           "it was reviewed. It is back in Implement for a new review.")
    else:
        forget_review(item)
    return {"state": "rebased", "behind": behind, "base": target, **({"retargeted": True} if merged else {})}


def retarget(c, it, default):
    """Point the story's PR at the default branch (auto-merge is disarmed first). Dies, leaving the record, when GitHub refuses."""
    ref = parse_pr_ref(c, it["pr"])
    if not ref:
        return
    cancel_auto_merge(*ref)
    view = pr_view(*ref, check=False)
    if not view:
        die("could not read the story's PR; not retargeting it")
    name = default.split("/", 1)[1]
    if view.get("baseRefName") != name:
        p = gh("pr", "edit", str(ref[1]), "-R", ref[0], "--base", name, check=False)
        if p.returncode:
            die(f"could not retarget the PR onto {name}: {(p.stderr or p.stdout).strip()}")


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
    rec = load_data().get("stack", {}).get(a.item)
    if rec:  # a stacked story follows its blocker's branch, not the default branch
        res = stack_sync(c, it, wt, rec, default)
        out({**res, **({"stack": rec["branch"]} if res["state"] != "blocker-closed" else {})})
        return
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
