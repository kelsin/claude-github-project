"""Scheduling: which stories are actionable, file overlap and blocking, the board snapshot and the wait loop."""
import fnmatch
import os
import time
from .consts import ACTIONABLE, ALL_KEYS, COLUMNS, HOME, STORY_OPTION, WAITING_FIELD
from .util import Poll, age_seconds, strip_id, covers, die, norm_path, now_iso, out
from .gh import rest
from .store import cfg, load_data, load_json, lock_file, lock_holder, save_board, state_path, stop_requested, update_data, update_state
from .board import board_keys, clear_field, ensure_story_option, fetch_items, fetch_project, fields_by_name, get_item, parse_item, parse_pr_ref, rank, set_single
from .gitwt import cleanup_worktree
from .story import process_replies


def sync_story_field(c, live):
    """Show the hierarchy on the board: Waiting On = 'Another story' while a story has live blockers (and is not waiting on you)."""
    w = c["fields"]["waiting"]
    if not w.get("story"):  # a config from before this option existed: add it to the board now
        b = c["board"]
        field = fields_by_name(fetch_project(b["kind"], b["owner"], b["number"]))[WAITING_FIELD]
        w["story"] = ensure_story_option(field)
        save_board(c)
    for i in live:
        if i["blockedBy"] and not i["waitingOn"]:
            set_single(c, i["item"], w["id"], w["story"])
            i["waitingOn"] = STORY_OPTION
        elif not i["blockedBy"] and i["waitingOn"] == STORY_OPTION:
            clear_field(c, i["item"], w["id"])
            i["waitingOn"] = None


def hard_conflict(a, b, patterns):
    """True when two stories' declared touches share a file that would block (shared-file globs and directory overlaps never do)."""
    dirs = {f for f in a | b if f.endswith("/")}
    return any(not any(fnmatch.fnmatchcase(f, g) for g in patterns) for f in (a & b) - dirs)


def pick_compatible(c, actionable, live, in_flight):
    """Of the stories about to start work (Plan Approved, or Implement with no PR), keep the largest group whose declared files
    don't collide with each other or with a worker already running; the rest wait (deferred) so no worker is spent on a story
    that would only block. Greedy by fewest conflicts, board order breaking ties."""
    touches = load_data().get("touches", {})
    patterns = c["settings"]["sharedFiles"]
    files = lambda i: {norm_path(f) for f in touches.get(i["item"], [])}
    gate = lambda i: i["column"] == "plan_approved" or (i["column"] == "implement" and not i["pr"])
    cands = [i for i in actionable if gate(i)]
    running = [i for i in live if i["item"] in in_flight and gate(i)]
    clash = lambda a, b: hard_conflict(files(a), files(b), patterns)
    for i in cands:
        i["conflictsWith"] = [r["title"] for r in running if clash(i, r)]
    pool = [i for i in cands if not i["conflictsWith"]]
    chosen = []
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
    return [{"item": w["item"], "title": w.get("title"), "minutes": round(age_seconds(w.get("startedAt")) / 60)}
            for w in load_json(state_path(), {}).get("workers", []) if age_seconds(w.get("startedAt")) > limit]


def snapshot(c):
    holder = lock_holder(c["board"]["id"])
    if holder:
        die("this board is now being run by another session (cgp use <url> --takeover to take it back)", code=5)
    items = [parse_item(r, c) for r in fetch_items(c["board"]["id"])]
    items = [i for i in items if not i["archived"] and i["kind"] in ("issue", "draft")]
    process_replies(c, items)
    answered = set(load_data().get("answered", []))
    for i in items:
        if i["item"] in answered:
            i["answered"] = True
    for i in items:  # closed issues (merged PR, or closed by hand) are finished: file them under Done
        if i["closed"] and i["column"] != "done" and i["kind"] == "issue":
            set_single(c, i["item"], c["fields"]["status"]["id"], c["fields"]["status"]["options"]["done"])
            i["column"] = "done"
            cleanup_worktree(c, i)
    live = [i for i in items if i["column"] != "done"]
    counts = {k: sum(1 for i in items if i["column"] == k) for k in board_keys(c)}
    by_id = {i["item"]: i for i in live}

    def live_blocks(raw):  # a block holds only while the blocker is live and still ahead of the blocked story
        return {k: [b for b in v if b in by_id and rank(by_id[b]) > rank(by_id[k])]
                for k, v in (raw or {}).items() if k in by_id}
    blocks = live_blocks(load_data().get("blocks"))
    live_ids = set(by_id)
    for i in live:
        i["blockedBy"] = [by_id[b]["title"] for b in blocks.get(i["item"], [])]
        i["blockers"] = [{"title": by_id[b]["title"], "column": by_id[b]["column"], "waiting": by_id[b]["waiting"]}
                         for b in blocks.get(i["item"], [])]
    sync_story_field(c, live)
    # a block gates a story that is about to start work: plan approved, or implementing with no PR yet
    blocked = [i for i in live if i["blockedBy"] and (i["column"] == "plan_approved"
                                                      or (i["column"] == "implement" and not i["pr"]))]
    in_flight = {w["item"] for w in load_json(state_path(), {}).get("workers", [])}  # a worker already owns these
    actionable = [i for i in live if i["column"] in ACTIONABLE and not i["waiting"] and i not in blocked
                  and i["item"] not in in_flight]
    actionable.sort(key=lambda i: ACTIONABLE.index(i["column"]))
    actionable, deferred = pick_compatible(c, actionable, live, in_flight)
    waiting = [i for i in live if i["waiting"]]
    cap = c["settings"]["concurrency"]
    batch = [] if stop_requested() else actionable[: max(cap - len(in_flight), 0)] if cap > 0 else actionable  # a stop request dispatches nothing new
    status = "done" if not live else "work" if batch else "idle"  # idle also when the cap is full
    snap = {
        "status": status,
        "stopRequested": stop_requested(),
        "board": c["board"],
        "counts": counts,
        "remaining": len(live),
        "batch": batch,
        "inFlight": sorted(in_flight),
        "actionableTotal": len(actionable),
        "waitingOnYou": waiting,
        "blocked": [{"title": i["title"], "blockedBy": i["blockedBy"], "blockers": i["blockers"]} for i in blocked],
        "deferred": [{"title": i["title"], "conflictsWith": i["conflictsWith"]} for i in deferred],
        "stalled": stalled_workers(c),
        "items": items,
    }

    def upd(st):
        st["board"] = c["board"]
        st["counts"] = counts
        st["waiting"] = [{"title": i["title"], "url": i["url"], "column": i["column"]} for i in waiting]
        st["blockedCount"] = len(blocked)
        st["updatedAt"] = now_iso()
    update_state(upd)

    def prune(d):
        d["deferred"] = [i["item"] for i in deferred]
        d["blocks"] = {k: v for k, v in live_blocks(d.get("blocks")).items() if v}  # re-read under the lock: keeps blocks written meanwhile
        d["touches"] = {k: v for k, v in d.get("touches", {}).items() if k in live_ids}
        for k in ("reviewed", "cleanRebase", "asked"):
            d[k] = {i: v for i, v in d.get(k, {}).items() if i in live_ids}
        d["tainted"] = [i for i in d.get("tainted", []) if i in live_ids]
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
    items = [parse_item(r, c) for r in fetch_items(c["board"]["id"])]
    live = [i for i in items if not i["archived"] and i["kind"] in ("issue", "draft") and i["column"] != "done"]
    by_col = {k: [i for i in live if i["column"] == k] for k in board_keys(c)}
    done = sum(1 for i in items if not i["archived"] and i["column"] == "done")
    blocks = load_data().get("blocks", {})
    titles = {i["item"]: i["title"] for i in live}
    blocked = [{"title": i["title"], "blockedBy": [titles[b] for b in blocks.get(i["item"], []) if b in titles]} for i in live]
    lock = load_json(lock_file(c["board"]["id"]), None)
    session = (lock or {}).get("session")
    state = load_json(os.path.join(HOME, f"state-{strip_id(session)}.json"), {}) if session else {}
    workers = [{**w, "minutes": round(age_seconds(w.get("startedAt")) / 60)} for w in state.get("workers", [])]
    res = {"board": c["board"], "counts": {k: len(v) for k, v in by_col.items()}, "done": done,
           "waitingOnYou": [{"title": i["title"], "url": i["url"]} for i in live if i["waiting"]],
           "blocked": [b for b in blocked if b["blockedBy"]], "workers": workers,
           "loop": {"session": session, "heartbeatMinutes": round((time.time() - lock["at"]) / 60, 1)} if lock else None,
           "review": {k: [{"title": i["title"], "url": i["url"]} for i in by_col.get(k, [])] for k in ("plan_review", "pr_review")}}
    if a.json:
        out(res)
        return
    emoji = {k: e for k, _, e, _ in COLUMNS}
    names = {k: n for k, n, _, _ in COLUMNS}
    print(f"📋 {c['board']['title']}  {c['board']['url']}")
    print("   " + "  ".join(f"{emoji.get(k, '•')} {names.get(k, k)} {n}" for k, n in res["counts"].items()) + f"  🎉 Done {done}")
    beat = res["loop"] and res["loop"]["heartbeatMinutes"]
    print("🔁 loop: " + (f"running in session {session[:8]}, heartbeat {beat} min ago" if lock else "not running (/cgp:run)"))
    for title, rows in (("❓ Waiting on you", res["waitingOnYou"]), ("🙋 Plan Review", res["review"]["plan_review"]),
                        ("🚦 PR Review", res["review"]["pr_review"])):
        if rows:
            print(f"{title} ({len(rows)})")
            for r in rows:
                print(f"   {r['title']}  {r['url']}")
    for w in workers:
        print(f"{emoji.get(w['column'], '•')} {w['title']} · {w.get('phase', '?')}{' (' + w['detail'] + ')' if w.get('detail') else ''} · {w['minutes']} min")
    for b in res["blocked"]:
        print(f"⛓ {b['title']} waits for {', '.join(b['blockedBy'])}")


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
        if snap["status"] != "idle" or released or stopped or not poll.wait():
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


def would_cycle(blocks, item, other):
    seen, todo = set(), [other]
    while todo:
        n = todo.pop()
        if n == item:
            return True
        if n not in seen:
            seen.add(n)
            todo += blocks.get(n, [])
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
    patterns = c["settings"]["sharedFiles"]
    for o in items:
        if (o["item"] == a.item or o["kind"] != "issue" or o["closed"] or o["archived"]
                or o["column"] in ("todo", "done")):
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
                      "alreadyWaitsOnMe": a.item in blocks.get(o["item"], [])})
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
    def add(st):
        if would_cycle(st.get("blocks", {}), a.item, a.other):
            die("refusing: that would create a dependency cycle (if the other story now ranks behind this one, run `cgp block <this> --unblock` and proceed)")
        lst = st.setdefault("blocks", {}).setdefault(a.item, [])
        if a.other not in lst:
            lst.append(a.other)
    update_data(add)
    out({"item": a.item, "blockedBy": load_data()["blocks"][a.item]})
