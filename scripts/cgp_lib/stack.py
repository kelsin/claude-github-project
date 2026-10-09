"""Stacked stories (setting stackedStories). A story whose only blocker is another story of the same repo with an open PR from its own
cgp/<n> branch starts at once: its worktree is cut from that branch and its PR opens against it. The data key `stack` holds one record per
stacked story, {on: blocker item, branch: "cgp/<m>", pr: "repo#n" of the blocker's PR, tip: the blocker commit the story is built on,
files: the blocker PR's files, onto: set while the story is rebased onto the default branch}. Only cgp writes it (prepare, sync), never
from text anyone supplies. While it exists the story cannot be approved by anyone but the user and cannot be merged (pr.merge_target);
`cgp sync` retargets the PR once the blocker merged (gitwt.stack_sync) and clears it."""
import contextlib
import io
import os
from .util import out
from .gh import rest
from .store import load_data, update_data
from .board import fetch_items, parse_item, parse_pr_ref
from .gitutil import fetch, git, resolve_repo_path, wt_path
from .pr import pr_view, stack_of
from .policy import require_human

STACKABLE_COLUMNS = ("pr_review", "pr_approved")  # the blocker's PR is up for review or approved, but not merged


def cmd_stack(a):
    """Show a story's stack record; --clear (you, in a terminal) forgets it. Records are made by prepare and sync, never by hand."""
    rec = stack_of(a.item)
    if not a.clear:
        out({"item": a.item, "stack": rec})
        return
    require_human("`cgp stack --clear` can only be run by you, in a terminal, not from a script or a worker: it lifts the merge gate of a stacked story")
    update_data(lambda d: d.get("stack", {}).pop(a.item, None))
    out({"item": a.item, "stack": None, "cleared": rec is not None})


def pr_files(repo, pr):
    """Every path (old names of renames too) the PR changes. Dies when GitHub cannot be read."""
    return sorted({n for f in rest(f"repos/{repo}/pulls/{pr}/files") for n in (f["filename"], f.get("previous_filename") or f["filename"])})


def blocker_ref(rec):
    repo, num = rec["pr"].rsplit("#", 1)
    return repo, int(num)


def stale(rec):
    """Why the story's PR may not be opened or sent for review on its stack right now (None when it may): the blocker's PR is not
    readable, or it was pushed to since the story was built on it (cgp sync rebases the story onto the new commit). A blocker that
    merged or closed is the business of cgp sync."""
    view = pr_view(*blocker_ref(rec), check=False)
    if not view:
        return "the blocker's PR could not be read"
    if view.get("state") == "OPEN" and view.get("headRefOid") != rec["tip"]:
        return f"{rec['branch']} was pushed to since this story was built on it: run cgp sync first"
    return None


def stackable(c, dep, by_id, blocks, data):
    """(blocker item, PR ref, PR view) when `dep` (a story about to start) may be built on its only blocker's open PR, else None.
    Needs the setting, one effective blocker on the board that is an issue of the same repo in PR Review or PR Approved and not stacked
    itself, no dependency cycle through the stack, an open same-repo PR from the blocker's own branch, issues of both written by someone
    you trust, and no worktree yet (unless the story is stacked already). Any doubt, any gh error: None, the story waits as before."""
    from .sched import would_cycle  # sched imports this module
    from .story import author_trusted
    stack = data.get("stack", {})
    if not c["settings"].get("stackedStories") or dep["kind"] != "issue":
        return None
    if os.path.isdir(wt_path(dep)) and dep["item"] not in stack:
        return None
    ids = blocks.get(dep["item"], [])
    blocker = by_id.get(ids[0]) if len(ids) == 1 else None
    if not blocker or blocker["kind"] != "issue" or blocker["issueRepo"] != dep["issueRepo"] or blocker["column"] not in STACKABLE_COLUMNS \
            or blocker["item"] in stack:
        return None
    ref = parse_pr_ref(c, blocker["pr"])
    if not ref or ref[0] != blocker["issueRepo"] or would_cycle(blocks, dep["item"], blocker["item"], {k: [r["on"]] for k, r in stack.items()}):
        return None
    try:
        view = pr_view(*ref, check=False)
        if not view or view.get("state") != "OPEN" or view.get("headRefName") != f"cgp/{blocker['number']}" \
                or view.get("isCrossRepository") is not False or not view.get("headRefOid"):
            return None
        if not (author_trusted(dep) and author_trusted(blocker)):
            return None
    except SystemExit:
        return None
    return blocker, ref, view


def prepare_stack(c, it):
    """Called by `cgp prepare` before the worktree exists: the story's stack record, made now when the story may be stacked (see
    stackable) and the blocker's branch is on the remote at the commit of its PR's head. None when the story is not stacked."""
    rec = stack_of(it["item"])
    if rec or not c["settings"].get("stackedStories") or os.path.isdir(wt_path(it)):
        return rec
    try:
        with contextlib.redirect_stderr(io.StringIO()):  # die() prints before it exits
            return make_record(c, it)
    except SystemExit:
        return None


def make_record(c, it):
    from .sched import effective_blocks, native_edges, parent_edges, starting
    items = [parse_item(r, c) for r in fetch_items(c["board"]["id"], bool(c["settings"]["nativeDependencies"]))]
    live = [i for i in items if not i["archived"] and i["kind"] in ("issue", "draft") and i["column"] != "done"]
    by_id, data = {i["item"]: i for i in live}, load_data()
    if not starting(it) or it["item"] in parent_edges(by_id, data):
        return None
    blocks = effective_blocks(live, data, native_edges(live) if c["settings"]["nativeDependencies"] else {})
    found = stackable(c, it, by_id, blocks, data)
    if not found:
        return None
    blocker, ref, view = found
    base = resolve_repo_path(c, it["issueRepo"])
    fetch(base)
    branch = view["headRefName"]
    if git(base, "rev-parse", "--verify", "-q", f"refs/heads/cgp/{it['number']}", check=False) \
            or git(base, "rev-parse", "--verify", "-q", f"origin/cgp/{it['number']}", check=False):
        return None  # a branch of its own exists already: it is not cut from the blocker now
    tip = git(base, "rev-parse", "--verify", "-q", f"origin/{branch}", check=False)
    if not tip or tip != view["headRefOid"]:
        return None
    rec = {"on": blocker["item"], "branch": branch, "pr": f"{ref[0]}#{ref[1]}", "tip": tip, "files": pr_files(*ref)}
    update_data(lambda d: d.setdefault("stack", {}).__setitem__(it["item"], rec))
    return rec

