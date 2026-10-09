"""Scheduling: which stories are actionable, file overlap and blocking, the board snapshot and the wait loop."""
import fnmatch
import json
import os
import sys
import time
from .consts import ACTIONABLE, ALL_KEYS, COLUMNS, HOME, STORY_OPTION, WAITING_FIELD
from .util import Poll, age_seconds, printable, strip_id, covers, die, norm_path, now_iso, out
from .gh import gh, rest
from .store import cfg, load_data, load_json, lock_file, lock_holder, state_path, stop_requested, update_board, update_data, update_state
from .board import board_keys, clear_field, ensure_story_option, fetch_items, fetch_project, fields_by_name, get_item, known_repo, parse_item, parse_pr_ref, rank, set_single
from .gitutil import wt_path
from .gitwt import cleanup_worktree
from .notify import check
from .epics import close_finished, parent_edges
from .auto_intake import run_intake
from .repoconf import merged_globs
from .policy import current_rating
from .flakes import prune as prune_flakes
from .story import ask_user, process_replies, send_back
from .stack import blocker_ref, pr_files, stackable
from .pr import pr_view
from .rules import pending, rules_due


def sync_story_field(c, live, held_back=frozenset(), in_flight=frozenset()):
    """Show why a story is not starting: Waiting On = 'Another story' while it has live blockers or is held back (overlap deferral,
    queued behind the concurrency cap), and not waiting on you. The marker clears once none of that holds. 'You' is never touched.
    A story a worker already owns is never marked, but a stale marker on it is still cleared."""
    w = c["fields"]["waiting"]
    if not w.get("story"):  # a config from before this option existed: add it to the board now
        b = c["board"]
        field = fields_by_name(fetch_project(b["kind"], b["owner"], b["number"]))[WAITING_FIELD]
        w["story"] = ensure_story_option(field)
        update_board(lambda cur: cur["fields"]["waiting"].__setitem__("story", w["story"]))
    for i in live:
        desired = i["item"] not in in_flight and ((bool(i["blockedBy"]) and not i.get("stackOn")) or i["item"] in held_back)
        if desired and not i["waitingOn"]:
            set_single(c, i["item"], w["id"], w["story"])
            i["waitingOn"] = STORY_OPTION
        elif not desired and i["waitingOn"] == STORY_OPTION:
            clear_field(c, i["item"], w["id"])
            i["waitingOn"] = None


NATIVE_OVERFLOW = "native:overflow"  # stands in for GitHub dependencies that were not read (see board.NATIVE_LIMIT)


def local_blocks(by_id, raw):
    """cgp's own overlap blocks: one holds only while the blocker is live and still ahead of the blocked story."""
    return {k: [b for b in v if b in by_id and rank(by_id[b]) > rank(by_id[k])] for k, v in (raw or {}).items() if k in by_id}


def ordered_blocks(by_id, raw):
    """Epic ordering (data["epicOrder"], written by native_order when GitHub would not take the edge): holds while the blocker is
    live, whatever the ranks."""
    return {k: [b for b in v if b in by_id] for k, v in (raw or {}).items() if k in by_id}


def native_edges(live):
    """GitHub's own `blockedBy` edges between live stories on this board: story -> blockers that are still open. Rank does not
    matter, and a blocker that is not on the board is ignored. A story with more dependencies than were read fails closed."""
    by_ref = {(i["issueRepo"].lower(), i["number"]): i for i in live if i["kind"] == "issue" and i["issueRepo"]}
    edges = {}
    for i in live:
        ids = []
        for b in i.get("nativeBlockedBy", []):
            o = by_ref.get(((b["repo"] or "").lower(), b["number"]))
            if b["state"] == "OPEN" and o and not o["closed"] and o["item"] != i["item"]:
                ids.append(o["item"])
        if i.get("nativeOverflow"):
            ids.append(NATIVE_OVERFLOW)
        if ids:
            edges[i["item"]] = ids
    return edges


def merge_edges(*graphs):
    merged = {}
    for g in graphs:
        for k, v in g.items():
            merged[k] = merged.get(k, []) + [b for b in v if b not in merged.get(k, [])]
    return merged


def effective_blocks(live, data, native=None):
    """Story -> the live stories that hold it back: cgp's overlap blocks, epic ordering and GitHub's dependencies together.
    A local overlap block that closes a cycle is dropped (it is only a scheduling hint); what cannot be dropped stays, see native_cycles."""
    by_id = {i["item"]: i for i in live}
    fixed = merge_edges(ordered_blocks(by_id, data.get("epicOrder")), parent_edges(by_id, data),
                        native_edges(live) if native is None else native)
    local = local_blocks(by_id, data.get("blocks"))
    merged = merge_edges(fixed, local)
    for k, v in local.items():
        for b in v:
            if b not in fixed.get(k, []):
                merged[k].remove(b)
                if would_cycle(merged, k, b):  # the other edges already lead from b back to k: this one only closes a cycle
                    continue
                merged[k].append(b)
    return merged


def native_cycles(live, data, native=None):
    """Live stories on a dependency cycle made only of edges cgp cannot drop (GitHub's and epic ordering): they wait on each other forever."""
    by_id = {i["item"]: i for i in live}
    g = merge_edges(ordered_blocks(by_id, data.get("epicOrder")), parent_edges(by_id, data), native_edges(live) if native is None else native)
    return [k for k in g if any(would_cycle(g, k, b) for b in g[k])]


def unlock_counts(blocks, live_ids):
    """Story -> how many distinct live stories wait on it, directly or through a chain (a story never counts itself, a cycle is safe)."""
    deps = {}
    for k, bs in blocks.items():
        for b in bs:
            if b in live_ids and k in live_ids:
                deps.setdefault(b, []).append(k)
    counts = {}
    for i in live_ids:
        seen, todo = set(), list(deps.get(i, []))
        while todo:
            n = todo.pop()
            if n not in seen and n != i:
                seen.add(n)
                todo.extend(deps.get(n, []))
        counts[i] = len(seen)
    return counts


def block_rows(by_id, ids):
    return [by_id[b] if b in by_id else {"title": "more GitHub dependencies than cgp reads", "column": None, "waiting": False} for b in ids]


def ask_about_cycle(c, live, members):
    """One question for a dependency cycle that only a person can break; asked on its first story and not again while that story waits."""
    by_id = {i["item"]: i for i in live}
    first = min((by_id[m] for m in members), key=rank)
    if first["waiting"] or first["kind"] != "issue":
        return
    names = ", ".join(f"{by_id[m]['title']} ({by_id[m]['issueRepo']}#{by_id[m]['number']})" for m in sorted(members, key=lambda m: rank(by_id[m])))
    ask_user(c, first["item"], f"These stories wait on each other through GitHub dependencies (blocked by), so none can start: {names}. "
                               "Remove one of the dependencies on GitHub, then reply here.")
    first["waiting"], first["waitingOn"] = True, "You"


def hard_conflict(a, b, patterns):
    """True when two stories' declared touches share a file that would block (shared-file globs and directory overlaps never do)."""
    dirs = {f for f in a | b if f.endswith("/")}
    return any(not any(fnmatch.fnmatchcase(f, g) for g in patterns) for f in (a & b) - dirs)


def starting(i):
    """A story about to start work (blocks hold only these): Plan Approved, Implement with no PR yet, or a Todo story that skips planning."""
    return i["column"] == "plan_approved" or (i["column"] == "implement" and not i["pr"]) or (i["column"] == "todo" and i["skipPlan"])


def pick_compatible(c, actionable, live, in_flight, slots=None):
    """Of the stories about to start work (Plan Approved, or Implement with no PR), keep the largest group whose declared files
    don't collide with each other or with a worker already running; the rest wait (deferred) so no worker is spent on a story
    that would only block. Greedy by fewest conflicts, board order breaking ties; with a limit of `slots` free workers, plainly in
    dispatch order (column, priority, most stories unlocked) so a low-priority story never takes the slot of a high-priority one."""
    touches = load_data().get("touches", {})
    patterns = merged_globs(c, "sharedFiles", {i["issueRepo"] for i in actionable})
    files = lambda i: {norm_path(f) for f in touches.get(i["item"], [])}
    gate = lambda i: i["column"] == "plan_approved" or (i["column"] == "implement" and not i["pr"])
    cands = [i for i in actionable if gate(i)]
    running = [i for i in live if i["item"] in in_flight and gate(i)]
    clash = lambda a, b: a["issueRepo"] == b["issueRepo"] and hard_conflict(files(a), files(b), patterns)  # paths are per repo
    for i in cands:
        i["conflictsWith"] = [r["title"] for r in running if clash(i, r)]
    pool = [i for i in cands if not i["conflictsWith"]]
    chosen = []
    if slots is not None:
        for i in pool:
            hit = next((o for o in chosen if clash(i, o)), None)
            if hit:
                i["conflictsWith"] = [hit["title"]]
            elif len(chosen) < slots:
                chosen.append(i)
        deferred = [i for i in cands if i.get("conflictsWith")]
        return [i for i in actionable if i not in deferred], deferred
    while pool:
        pick = min(pool, key=lambda i: sum(clash(i, o) for o in pool if o is not i))
        chosen.append(pick)
        for o in pool:
            if o is not pick and clash(pick, o):
                o["conflictsWith"] = [pick["title"]]
        pool = [o for o in pool if o is not pick and not o.get("conflictsWith")]
    deferred = [i for i in cands if i not in chosen]
    return [i for i in actionable if i not in deferred], deferred


def stalled_workers(c):
    """Workers older than the maxWorkerMinutes setting: a hung or silently dead sub-agent otherwise owns its story (and, with a
    concurrency cap, a slot) forever. The loop stops them and counts the run as no progress."""
    limit = c["settings"]["maxWorkerMinutes"] * 60
    if not limit:
        return []
    return [{"item": w["item"], "title": w.get("title"), "startedAt": w.get("startedAt"), "minutes": round(age_seconds(w.get("startedAt")) / 60)}
            for w in load_json(state_path(), {}).get("workers", []) if age_seconds(w.get("startedAt")) > limit]


def send_back_stacks(c, live, by_id, in_flight):
    """A stacked story waiting in PR Review or PR Approved goes back to Implement when what it is built on changed: its blocker went
    back to an earlier column, its PR was closed, or it was pushed to and the new commits touch files this story touches (shared files
    excepted). Waits while the story's worker runs. A gh error decides nothing this cycle."""
    data = load_data()
    patterns = merged_globs(c, "sharedFiles", {i["issueRepo"] for i in live})
    for i in live:
        rec = data.get("stack", {}).get(i["item"])
        blocker = by_id.get(rec["on"]) if rec else None
        if not blocker or i["column"] not in ("pr_review", "pr_approved") or i["item"] in in_flight:
            continue
        why = None
        if ALL_KEYS.index(blocker["column"]) < ALL_KEYS.index("pr_review"):
            why = f"the story it is stacked on ({blocker['title']}) went back to {blocker['column']}"
        else:
            try:
                view = pr_view(*blocker_ref(rec), check=False)
                if view and view["state"] == "CLOSED":
                    why = f"the PR of the story it is stacked on ({blocker['title']}) was closed"
                elif view and view["state"] == "OPEN" and view["headRefOid"] != rec["tip"]:
                    theirs = set(rec["files"]) | set(pr_files(*blocker_ref(rec)))
                    hit = sorted(f for f in story_files(c, data, i) & theirs if not any(fnmatch.fnmatchcase(f, g) for g in patterns))
                    if hit:
                        why = f"the story it is stacked on ({blocker['title']}) was pushed to and now changes files this one changes ({', '.join(hit[:3])})"
            except SystemExit:
                continue
        if why:
            try:
                send_back(c, i["item"], f"Back in Implement: {why}. The PR needs a `cgp sync` and a new review.")
            except SystemExit:
                continue
            i["column"] = "implement"


def snapshot(c):
    holder = lock_holder(c["board"]["id"])
    if holder:
        die("this board is now being run by another session (cgp use <url> --takeover to take it back)", code=5)
    items = [parse_item(r, c) for r in fetch_items(c["board"]["id"], bool(c["settings"]["nativeDependencies"]))]
    items = [i for i in items if not i["archived"] and i["kind"] in ("issue", "draft")]
    process_replies(c, items)
    answered = set(load_data().get("answered", []))
    for i in items:
        if i["item"] in answered:
            i["answered"] = True
    def file_closed():  # closed issues (merged PR, or closed by hand) are finished: file them under Done
        for i in items:
            if i["closed"] and i["column"] != "done" and i["kind"] == "issue":
                set_single(c, i["item"], c["fields"]["status"]["id"], c["fields"]["status"]["options"]["done"])
                i["column"] = "done"
    def sweep_done():  # a finished story's worktree goes unless it holds work that is not saved elsewhere; tried again hourly if kept
        kept = load_json(state_path(), {}).get("worktreeKept", {})
        now, fetched, seen = time.time(), {}, {}
        for i in items:
            if i["kind"] != "issue" or i["column"] != "done" or i["item"] in in_flight or not os.path.isdir(wt_path(i)) \
                    or not known_repo(c, i["issueRepo"]):
                continue
            if i["item"] in kept and now - kept[i["item"]]["at"] < 3600:
                seen[i["item"]] = kept[i["item"]]
                continue
            reason = cleanup_worktree(c, i, fetched)
            if reason:
                seen[i["item"]] = {"at": now, "reason": reason}
        if seen != kept:
            update_state(lambda st: st.__setitem__("worktreeKept", seen))
    in_flight = {w["item"] for w in load_json(state_path(), {}).get("workers", [])}  # a worker already owns these
    file_closed()
    close_finished(c, items)  # a story split into sub-stories is closed once they are all done (see epics.py)
    created = set()  # items intake added to the board just now: not in `items`, but their data must survive the prune below
    try:  # opt-in failing-main and dependency-PR intake (see auto_intake.py); its stories are picked up by the next cycle
        problems = run_intake(c, items, created)
    except (SystemExit, Exception) as e:
        problems = [str(e)]
    file_closed()
    sweep_done()
    live = [i for i in items if i["column"] != "done"]
    by_id = {i["item"]: i for i in live}
    send_back_stacks(c, live, by_id, in_flight)
    counts = {k: sum(1 for i in items if i["column"] == k) for k in board_keys(c)}

    data = load_data()
    for i in items:
        i["rating"] = current_rating(data, i)
    native = native_edges(live) if c["settings"]["nativeDependencies"] else {}
    members = native_cycles(live, data, native)
    if members:
        ask_about_cycle(c, live, members)
    blocks = effective_blocks(live, data, native)
    live_ids = set(by_id)
    unlocks = unlock_counts(blocks, live_ids)
    for i in live:
        i["unlocks"] = unlocks[i["item"]]
        rows = block_rows(by_id, blocks.get(i["item"], []))
        i["blockedBy"] = [r["title"] for r in rows]
        i["blockers"] = [{"title": r["title"], "column": r["column"], "waiting": r["waiting"]} for r in rows]
    # a block gates a story that is about to start work (see starting)
    parents = parent_edges(by_id, data)  # a story waiting for its sub-stories is never dispatched, whatever its column
    for i in live:  # stackedStories: a starting story whose only blocker has an open PR starts at once, built on that PR's branch (see stack.py)
        if i["blockedBy"] and starting(i) and i["item"] not in parents:
            rec = data.get("stack", {}).get(i["item"])
            if i["item"] in in_flight:  # its worker owns it: no gh call to say what it is stacked on
                blocker = by_id.get(rec["on"]) if rec and rec["on"] in blocks.get(i["item"], []) else None
            else:
                found = stackable(c, i, by_id, blocks, data)
                blocker = found and found[0]
            if blocker:
                i["stackOn"] = {"item": blocker["item"], "title": blocker["title"], "pr": blocker["pr"]}
    blocked = [i for i in live if i["blockedBy"] and not i.get("stackOn") and (starting(i) or i["item"] in parents)]
    actionable = [i for i in live if i["column"] in ACTIONABLE and not i["waiting"] and not i["held"] and i not in blocked
                  and i["item"] not in in_flight]
    actionable.sort(key=lambda i: (ACTIONABLE.index(i["column"]), i["priorityRank"], -i["unlocks"]))  # stable: board order breaks remaining ties
    cap = c["settings"]["concurrency"]
    free = max(cap - len(in_flight), 0)
    # Todo and Plan stories would starve behind the later columns, which are re-dispatched every cycle: while one is actionable and no
    # planning worker runs, later-column stories (in flight and new) may use at most cap-1 slots. A cap of 1 reserves nothing.
    planning = [i for i in actionable if i["column"] == "plan" or (i["column"] == "todo" and not i["skipPlan"])]
    planning_busy = any(i["item"] in in_flight and i["column"] in ("plan", "todo") for i in live)
    stop = stop_requested()
    if cap > 1 and planning and not planning_busy:
        later = [i for i in actionable if i not in planning]
        later_slots = max(free - 1, 0)
        later, deferred = pick_compatible(c, later, live, in_flight, later_slots)
        later = later[:later_slots]
        picked = later + planning[:free - len(later)]
        actionable = [i for i in actionable if i not in deferred]
        batch = [i for i in actionable if i in picked]
    else:
        actionable, deferred = pick_compatible(c, actionable, live, in_flight, free if cap > 0 else None)
        batch = actionable[:free] if cap > 0 else actionable
    waiting = [i for i in live if i["waiting"]]
    if stop:
        batch = []  # a stop request dispatches nothing new
    # why a story is not starting: it overlaps a rival, or it waits for a free worker slot (a stop makes the batch empty on purpose,
    # so it queues nothing)
    queued = [i for i in actionable if i not in batch] if cap > 0 and not stop else []  # (stop: see held_back below)
    reasons = {i["item"]: "waits for " + ", ".join(i["blockedBy"]) for i in blocked}
    reasons.update({i["item"]: "overlaps with " + ", ".join(i["conflictsWith"]) for i in deferred if starting(i)})
    reasons.update({i["item"]: f"queued: all {cap} worker slots are busy" for i in queued})
    held_back = set() if stop else {i["item"] for i in deferred if starting(i)} | {i["item"] for i in queued}
    sync_story_field(c, live, held_back, in_flight)
    fresh = created - live_ids  # stories intake just filed: live work this cycle
    status = "done" if not live and not fresh else "work" if batch else "idle"  # idle also when the cap is full
    stalled = stalled_workers(c)
    check(c, items, stalled)
    snap = {
        "status": status,
        "stopRequested": stop_requested(),
        "board": c["board"],
        "counts": counts,
        "remaining": len(live) + len(fresh),
        "batch": batch,
        "inFlight": sorted(in_flight),
        "actionableTotal": len(actionable),
        "waitingOnYou": waiting,
        "blocked": [{"title": i["title"], "blockedBy": i["blockedBy"], "blockers": i["blockers"], "reason": reasons[i["item"]]} for i in blocked],
        "deferred": [{"title": i["title"], "conflictsWith": i["conflictsWith"], "reason": reasons.get(i["item"])} for i in deferred],
        "queued": [{"title": i["title"], "reason": reasons[i["item"]]} for i in queued],
        "stalled": stalled,
        "held": [i["title"] for i in live if i["held"]],
        "rulesDue": [r for r in c["repos"] if rules_due(c, data, r)],
        "items": items,
    }

    def upd(st):
        st["board"] = c["board"]
        st["counts"] = counts
        st["waiting"] = [{"title": i["title"], "url": i["url"], "column": i["column"]} for i in waiting]
        st["blockedCount"] = len(blocked)
        st["updatedAt"] = now_iso()
        if problems:  # None = no scan this cycle: leave an earlier error in place
            st["intakeError"] = "; ".join(problems)[:500]
        elif problems is not None:
            st.pop("intakeError", None)
    update_state(upd)

    def prune(d):
        d["deferred"] = [i["item"] for i in deferred]
        d["reasons"] = reasons  # for `cgp status`, which does not build a snapshot
        d["blocks"] = {k: v for k, v in local_blocks(by_id, d.get("blocks")).items() if v}  # re-read under the lock: keeps blocks written meanwhile
        d["epicOrder"] = {k: v for k, v in ordered_blocks(by_id, d.get("epicOrder")).items() if v}
        d["touches"] = {k: v for k, v in d.get("touches", {}).items() if k in live_ids}
        snaps = {k: v for k, v in d.get("approvedTouches", {}).items() if k in live_ids}
        for i in live:  # the files a story declared when it was approved (see story.snapshot_touches); replanning forgets them
            if i["column"] in ("plan_approved", "implement") and i["item"] not in snaps:
                snaps[i["item"]] = list(d.get("touches", {}).get(i["item"], []))
            elif i["column"] in ("todo", "plan", "plan_review") and i["item"] not in d.get("parents", {}):  # a sub-story keeps what its parent's plan gave it
                snaps.pop(i["item"], None)
        d["approvedTouches"] = snaps
        d["splits"] = {k: v for k, v in d.get("splits", {}).items() if k in live_ids}
        splits = {k: v for k, v in d.get("approvedSplits", {}).items() if k in live_ids}
        for i in live:  # the split declared when the plan was approved: all `cgp split` creates
            if i["column"] in ("plan_approved", "implement") and i["item"] not in splits and d["splits"].get(i["item"]):
                splits[i["item"]] = list(d["splits"][i["item"]])
            elif i["column"] in ("todo", "plan", "plan_review"):
                splits.pop(i["item"], None)
        d["approvedSplits"] = splits
        d["children"] = {k: v for k, v in d.get("children", {}).items() if k in live_ids}
        d["parents"] = {k: v for k, v in d.get("parents", {}).items() if k in live_ids}
        d["epicAsked"] = {k: v for k, v in d.get("epicAsked", {}).items() if k in live_ids}
        d["policyPlans"] = [i for i in d.get("policyPlans", []) if i in live_ids and by_id[i]["column"] not in ("todo", "plan", "plan_review")]
        for k in ("reviewed", "cleanRebase", "asked", "ratings", "policy"):
            d[k] = {i: v for i, v in d.get(k, {}).items() if i in live_ids or (k == "ratings" and i in created)}
        d["tainted"] = [i for i in d.get("tainted", []) if i in live_ids]
        live_prs = {f"{r[0]}#{r[1]}" for r in (parse_pr_ref(c, i.get("pr")) for i in live) if r}
        d["reruns"] = {k: v for k, v in d.get("reruns", {}).items() if k in live_prs}
        d["resume"] = {k: v for k, v in d.get("resume", {}).items() if k.split("|")[0] in live_ids}
        d["stack"] = {k: v for k, v in d.get("stack", {}).items() if k in live_ids}  # live_ids: every story on the board, workers' too
        prune_flakes(d)
    update_data(prune)
    return snap


def cmd_list(a):
    snap = snapshot(cfg())
    if a.brief:
        snap = {k: v for k, v in snap.items() if k != "items"}
    out(snap)


def cmd_status(a):
    """What a person wants to know, without the snapshot's side effects (it moves nothing, clears nothing, needs no lock)."""
    c = cfg()
    items = [parse_item(r, c) for r in fetch_items(c["board"]["id"], bool(c["settings"]["nativeDependencies"]))]
    live = [i for i in items if not i["archived"] and i["kind"] in ("issue", "draft") and i["column"] != "done"]
    by_col = {k: [i for i in live if i["column"] == k] for k in board_keys(c)}
    done = sum(1 for i in items if not i["archived"] and i["column"] == "done")
    by_id = {i["item"]: i for i in live}
    blocks = effective_blocks(live, load_data(), native_edges(live) if c["settings"]["nativeDependencies"] else {})
    blocked = [{"title": i["title"], "blockedBy": [r["title"] for r in block_rows(by_id, blocks.get(i["item"], []))]} for i in live]
    github = [{"title": i["title"], "blockedBy": [{"story": f"{b['repo']}#{b['number']}", "author": b["author"], "onBoard": any(
        o["issueRepo"] == b["repo"] and o["number"] == b["number"] for o in live)} for b in i["nativeBlockedBy"] if b["state"] == "OPEN"]}
              for i in live if c["settings"]["nativeDependencies"] and any(b["state"] == "OPEN" for b in i["nativeBlockedBy"])]
    lock = load_json(lock_file(c["board"]["id"]), None)
    session = (lock or {}).get("session")
    state = load_json(os.path.join(HOME, f"state-{strip_id(session)}.json"), {}) if session else {}
    workers = [{**w, "minutes": round(age_seconds(w.get("startedAt")) / 60)} for w in state.get("workers", [])]
    data = load_data()
    asked = data.get("asked", {})
    why = {i["title"]: data.get("reasons", {})[i["item"]] for i in live if lock and i["item"] in data.get("reasons", {})}  # stale once the loop is gone
    stale = [i["title"] for i in live if i["waitingOn"] == "You" and i["item"] not in asked]
    res = {"board": c["board"], "counts": {k: len(v) for k, v in by_col.items()}, "done": done,
           "waitingOnYou": [{"title": i["title"], "url": i["url"]} for i in live if i["waiting"]],
           "blocked": [b for b in blocked if b["blockedBy"]], "githubBlockedBy": github, "workers": workers,
           "held": [i["title"] for i in live if i["held"]], "notStarting": why, "waitingOnYouNothingAsked": stale, "autoApprove": c["settings"]["autoApprove"],
           "rulesProposals": pending(c),
           "loop": {"session": session, "heartbeatMinutes": round((time.time() - lock["at"]) / 60, 1)} if lock else None,
           "review": {k: [{"title": i["title"], "url": i["url"]} for i in by_col.get(k, [])] for k in ("plan_review", "pr_review")}}
    if a.json:
        out(res)
        return
    show = lambda text: print(printable(text))  # titles come from the board: no terminal control or bidi characters
    emoji = {k: e for k, _, e, _ in COLUMNS}
    names = {k: n for k, n, _, _ in COLUMNS}
    show(f"📋 {c['board']['title']}  {c['board']['url']}")
    show("   " + "  ".join(f"{emoji.get(k, '•')} {names.get(k, k)} {n}" for k, n in res["counts"].items()) + f"  🎉 Done {done}")
    beat = res["loop"] and res["loop"]["heartbeatMinutes"]
    show("🔁 loop: " + (f"running in session {session[:8]}, heartbeat {beat} min ago" if lock else "not running (/cgp:run)"))
    for title, rows in (("❓ Waiting on you", res["waitingOnYou"]), ("🙋 Plan Review", res["review"]["plan_review"]),
                        ("🚦 PR Review", res["review"]["pr_review"])):
        if rows:
            show(f"{title} ({len(rows)})")
            for r in rows:
                show(f"   {r['title']}  {r['url']}")
    for w in workers:
        show(f"{emoji.get(w['column'], '•')} {w['title']} · {w.get('phase', '?')}{' (' + w['detail'] + ')' if w.get('detail') else ''} · {w['minutes']} min")
    if res["held"]:
        show(f"⏸ On hold ({len(res['held'])}): {', '.join(res['held'])}")
    if res["autoApprove"] != "plan:never,pr:never":
        show(f"🤖 auto-approval policy: {res['autoApprove']} (autoApproveFiles: {', '.join(c['settings']['autoApproveFiles']) or 'none'})")
    for r in res["rulesProposals"]:
        show(f"📝 proposed house rules for {r['repo']} differ from its default branch: {r['path']}")
    for b in res["blocked"]:
        show(f"⛓ {b['title']} waits for {', '.join(b['blockedBy'])}")
    for b in res["blocked"]:
        why.pop(b["title"], None)  # already shown above
    for title, reason in why.items():
        show(f"⏳ {title} {reason}")
    for title in stale:
        show(f"❔ {title} is waiting on you but nothing was asked: clear the Waiting On field")
    for g in res["githubBlockedBy"]:
        shown = ", ".join(f"{b['story']} (opened by {b['author'] or '?'}{'' if b['onBoard'] else '; not on this board, ignored'})" for b in g["blockedBy"])
        show(f"🔗 {g['title']} is blocked by {shown} on GitHub")


def cmd_wait(a):
    c = cfg()
    poll = Poll(a.timeout, a.interval or c["settings"]["pollSeconds"])
    started = None
    while True:
        snap = snapshot(c)
        started = snap["inFlight"] if started is None else started
        # a worker finishing frees its story for the next dispatch, so it ends the wait too
        released = snap["inFlight"] != started
        # with a stop requested the loop is only waiting for its workers; once none are left there is nothing to wait for
        stopped = snap["stopRequested"] and not snap["inFlight"]
        # `done` with a worker still running (its issue was closed, or a stop is pending) keeps waiting for that worker
        over = snap["status"] == "work" or (snap["status"] == "done" and not snap["inFlight"])
        if over or released or stopped or not poll.wait():
            snap.pop("items")
            out(snap)
            return


def story_files(c, st, it):
    files = set(norm_path(f) for f in st.get("touches", {}).get(it["item"], []))
    ref = parse_pr_ref(c, it.get("pr"))
    if ref:
        for f in rest(f"repos/{ref[0]}/pulls/{ref[1]}/files"):
            files |= {f["filename"], f.get("previous_filename") or f["filename"]}
    return files


def would_cycle(blocks, item, other, native=None):
    """True when making `item` wait for `other` would close a cycle in `blocks` plus the `native` (GitHub / epic) edges."""
    seen, todo = set(), [other]
    while todo:
        n = todo.pop()
        if n == item:
            return True
        if n not in seen:
            seen.add(n)
            todo += blocks.get(n, []) + (native or {}).get(n, [])
    return False


def cmd_touches(a):
    if a.paths:
        update_data(lambda d: d.setdefault("touches", {}).__setitem__(a.item, [norm_path(p) for p in a.paths if p.strip()]))
    out(load_data().get("touches", {}).get(a.item, []))


def cmd_overlap(a):
    c = cfg()
    st = load_data()
    me = get_item(c, a.item)
    mine = story_files(c, st, me)
    items = [parse_item(r, c) for r in fetch_items(c["board"]["id"])]
    blocks = st.get("blocks", {})
    found, unknown = [], []
    patterns = merged_globs(c, "sharedFiles", {me["issueRepo"]})
    for o in items:
        if (o["item"] == a.item or o["kind"] != "issue" or o["closed"] or o["archived"]
                or o["column"] in ("todo", "done") or o["issueRepo"] != me["issueRepo"]):  # paths are per repo
            continue
        theirs = story_files(c, st, o)
        if not theirs:
            if ALL_KEYS.index(o["column"]) >= ALL_KEYS.index("plan_approved"):
                unknown.append({"item": o["item"], "title": o["title"], "column": o["column"]})
            continue
        dirs = {f for f in mine | theirs if f.endswith("/")}
        both = (mine & theirs) - dirs
        shared = sorted(f for f in both if any(fnmatch.fnmatchcase(f, g) for g in patterns))
        files = sorted(both - set(shared))
        areas = sorted({f"{f} ~ {g}" for f in mine for g in theirs if (f in dirs or g in dirs) and (covers(f, g) or covers(g, f))})
        if not files and not areas and not shared:
            continue
        found.append({"item": o["item"], "title": o["title"], "number": o["number"], "column": o["column"],
                      "pr": o["pr"], "files": files, "shared": shared, "areas": areas, "ahead": rank(o) > rank(me),
                      "alreadyWaitsOnMe": a.item in blocks.get(o["item"], []) or st.get("stack", {}).get(a.item, {}).get("on") == o["item"]})  # the stack base is no block to ask for
    held = set(st.get("deferred", []))  # the loop is holding these back for me: waiting on them would deadlock
    block_on = [f["item"] for f in found if f["ahead"] and f["files"] and not f["alreadyWaitsOnMe"] and f["item"] not in held]
    suggest = ({"action": "declare", "why": "this story has no touches or PR yet: run `cgp touches` first"} if not mine
               else {"action": "block", "on": block_on} if block_on else {"action": "proceed"})
    out({"story": {"item": a.item, "touches": sorted(mine)}, "overlaps": found,
         "undeclared": unknown, "suggest": suggest})


def cmd_block(a):
    if a.unblock:
        def un(st):
            st.setdefault("blocks", {}).pop(a.item, None)
        update_data(un)
        out({"item": a.item, "blockedBy": []})
        return
    if not a.other:
        die("usage: cgp block <item> <other-item> | cgp block <item> --unblock")
    c = cfg()
    live = [i for i in (parse_item(r, c) for r in fetch_items(c["board"]["id"], bool(c["settings"]["nativeDependencies"])))
            if not i["archived"] and i["kind"] in ("issue", "draft") and i["column"] != "done"]
    by_id, data = {i["item"]: i for i in live}, load_data()
    native = merge_edges(ordered_blocks(by_id, data.get("epicOrder")), parent_edges(by_id, data),
                         native_edges(live) if c["settings"]["nativeDependencies"] else {})  # edges cgp reads but never writes

    def add(st):
        if would_cycle(st.get("blocks", {}), a.item, a.other, native):
            die("refusing: that would create a dependency cycle (if the other story now ranks behind this one, run `cgp block <this> --unblock` and proceed)")
        lst = st.setdefault("blocks", {}).setdefault(a.item, [])
        if a.other not in lst:
            lst.append(a.other)
    update_data(add)
    out({"item": a.item, "blockedBy": load_data()["blocks"][a.item]})


def native_order(c, item, blocker):
    """Make `item` (a parsed story) wait for `blocker` because a plan declared that order (epics). Overlap blocks are never written
    to GitHub: a native edge does not go away when ranks flip, so a mirrored one could deadlock. GitHub takes the edge when it can
    (setting on, both are issues); a draft, a refusal (old GHES, no permission, another org) or the setting off orders them in
    cgp's own data["epicOrder"] instead. Returns "github" or "local"."""
    if c["settings"]["nativeDependencies"] and item["kind"] == "issue" and blocker["kind"] == "issue":
        try:
            blocker_id = json.loads(gh("api", f"repos/{blocker['issueRepo']}/issues/{blocker['number']}").stdout)["id"]
            gh("api", "-X", "POST", f"repos/{item['issueRepo']}/issues/{item['number']}/dependencies/blocked_by", "-F", f"issue_id={blocker_id}")
            return "github"
        except (SystemExit, ValueError, KeyError):
            print("cgp: GitHub would not take the dependency; ordering these stories in cgp instead", file=sys.stderr)
    def add(d):
        waits = d.setdefault("epicOrder", {}).setdefault(item["item"], [])
        if blocker["item"] not in waits:
            waits.append(blocker["item"])
    update_data(add)
    return "local"
