"""Session binding (use/release/stop), worker registry and phases."""
import json
import os
import re
import sys
import time
from .consts import DEFAULT_PHASE, HOME, LOCKS, LOCK_STALE_SECONDS, PHASES, SCHEMA
from .util import die, now_iso, out, safe, strip_id
from .store import cfg, ensure_home, list_boards, load_board, load_json, lock_file, lock_holder, locked, save_json, sid, state_path, stop_path, update_state
from .board import get_item, migrate_columns, parse_board_url


def cmd_use(a):
    """Bind this session to a board and claim its loop. Exit 5: another live session runs it; 6: which board?"""
    keys = list_boards()
    boards = {k: load_board(k) for k in keys}
    if a.url:
        kind, owner, number = parse_board_url(a.url)
        key = next((k for k, b in boards.items()
                    if b["board"]["number"] == number and b["board"]["owner"].lower() == owner.lower()), None)
        if not key:
            die(f"that board is not set up yet; run /cgp:setup {a.url} first")
    elif load_json(state_path(), {}).get("boardKey") in boards:
        key = load_json(state_path(), {})["boardKey"]
    elif len(boards) == 1:
        key = keys[0]
    else:
        out({"boards": [{"title": b["board"]["title"], "url": b["board"]["url"]} for b in boards.values()]})
        die("several boards are set up; say which: cgp use <board-url>", code=6)
    with locked():  # check and claim atomically, so two sessions cannot both win
        holder = lock_holder(key)
        if holder and not a.takeover:
            out({"heldBy": holder["session"], "minutesSinceHeartbeat": round((time.time() - holder["at"]) / 60, 1)})
            die("another session is running this board's loop (use --takeover only if that session is gone)", code=5)
        os.makedirs(LOCKS, mode=0o700, exist_ok=True)
        save_json(lock_file(key), {"session": sid(), "at": time.time()})
    update_state(lambda st: st.update(boardKey=key, board=boards[key]["board"], workers=[]))
    if os.path.exists(stop_path()):
        os.remove(stop_path())  # a stop request left from an earlier run
    c = boards[key]
    res = {"board": c["board"], "session": sid(), "remoteControl": bool(c["settings"].get("remoteControl", 1))}
    if c.get("schema", 1) < SCHEMA:  # under the lock just claimed, so no loop can be running on the old columns
        if not c["settings"].get("autoMigrate", 1):
            res["migration"] = {"skipped": "autoMigrate is 0; this board keeps its old columns until: cgp migrate"}
        else:
            try:
                res["migration"] = migrate_columns(c)
            except SystemExit:  # the board is still in a layout the loop can run; the error is on stderr
                res["migration"] = {"error": "could not update the board's columns; it keeps its old ones (see stderr) and is retried next run"}
    out(res)


def cmd_session_title(a):
    """UserPromptSubmit hook: name the session after the board when /cgp:run or /cgp:setup starts it. Silent otherwise."""
    try:
        prompt = json.load(sys.stdin).get("prompt", "")
        m = re.match(r"\s*/cgp:(run|setup)\b\s*(\S*)", prompt)
        if not m:
            return
        name = None
        boards = [load_board(k)["board"] for k in list_boards()]
        if m.group(2).startswith("http"):
            _, owner, number = parse_board_url(m.group(2))
            found = next((b for b in boards if b["number"] == number and b["owner"].lower() == owner.lower()), None)
            name = found["title"] if found else f"{owner} project {number}"
        else:
            key = load_json(state_path(), {}).get("boardKey")
            found = next((b for b in boards if safe(b["id"]) == key), None) or (boards[0] if len(boards) == 1 else None)
            name = found["title"] if found else None
        if name:
            out({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": f"{'🚀' if m.group(1) == 'run' else '🛠️'} {name}"}})
    except (Exception, SystemExit):
        return  # a naming hook must never get in the way of the prompt


def cmd_release(a):
    key = load_json(state_path(), {}).get("boardKey")
    if key:
        lk = load_json(lock_file(key), None)
        if lk and lk.get("session") == sid():
            os.remove(lock_file(key))
    update_state(lambda st: st.update(boardKey=None, workers=[], counts={}, waiting=[], updatedAt=None))
    out({"released": key})


def stop_target(arg):
    """Stop-file path for the session to stop: this one by default (CGP_SESSION), else the holder of a board's lock."""
    if not arg and os.environ.get("CGP_SESSION"):
        return stop_path()
    if arg:
        boards = {k: load_board(k) for k in list_boards()}
        if arg.startswith("http"):
            _, owner, number = parse_board_url(arg)
            keys = [k for k, b in boards.items() if b["board"]["number"] == number and b["board"]["owner"].lower() == owner.lower()]
        else:
            keys = [k for k in boards if k == safe(arg)]
        if not keys:
            die("unknown board; pass a board URL or key (see: cgp use)")
    else:
        keys = [f[:-5] for f in sorted(os.listdir(LOCKS))] if os.path.isdir(LOCKS) else []
    live = [lk for lk in (load_json(lock_file(k), None) for k in keys)
            if lk and time.time() - lk.get("at", 0) < LOCK_STALE_SECONDS]
    if not live and not arg:
        return stop_path()  # nothing holds a lock: a session that never claimed a board (or none at all)
    if len(live) != 1:
        die("no live session found for that board" if arg else "several live sessions; run: cgp stop <board-url>")
    return os.path.join(HOME, f"stop-{strip_id(live[0]['session'])}")


def cmd_stop(a):
    ensure_home()
    path = stop_target(a.board)
    with open(path, "w") as f:
        f.write("0" if a.cancel else "1")
    out({"stopRequested": not a.cancel, "session": os.path.basename(path)[len("stop-"):]})


def set_phase(phase, detail="", item=None, pr=None):
    """Set (or with None, clear) the phase of the worker owning an item, or the PR 'owner/repo#n' it recorded.
    Returns its previous (phase, detail), or False when no such worker is registered, so a temporary phase can be put back."""
    prev = []

    def upd(st):
        for w in st["workers"]:
            if w["item"] == item or (pr and w.get("pr") == pr):
                prev.append((w.get("phase"), w.get("detail", "")))
                for k in ("phase", "detail", "phaseAt"):
                    w.pop(k, None)
                if phase:
                    w.update(phase=phase, detail=detail, phaseAt=now_iso())
    update_state(upd)
    return prev[0] if prev else False


def worker_pr(item, ref):
    """Remember which PR a worker owns, so ci-wait (which only knows the PR) can find its worker."""
    def upd(st):
        for w in st["workers"]:
            if w["item"] == item:
                w["pr"] = f"{ref[0]}#{ref[1]}"
    update_state(upd)


def cmd_worker(a):
    """start / stop / clear / phase. `start` takes the title (and, unless given, the column) from the board: an issue title is
    text anyone can write, so it must never travel through a shell command line."""
    if a.action == "start":
        it = get_item(cfg(), a.item)
        a.column, a.title = a.column or it["column"], it["title"]

    def upd(st):
        if a.action == "phase":
            for w in st["workers"]:
                if w["item"] == a.item:
                    w.update(phase=a.column, detail=(a.title or "")[:80], phaseAt=now_iso())
            return
        st["workers"] = [w for w in st["workers"] if w["item"] != a.item]
        if a.action == "start":
            w = {"item": a.item, "column": a.column, "title": a.title, "startedAt": now_iso()}
            if a.column in DEFAULT_PHASE:
                w.update(phase=DEFAULT_PHASE[a.column], phaseAt=now_iso())
            st["workers"].append(w)
    if a.action == "phase" and a.column not in PHASES:
        die(f"phase must be one of {list(PHASES)}")
    if a.action == "clear":
        update_state(lambda st: st.__setitem__("workers", []))
    else:
        update_state(upd)
    out(load_json(state_path(), {}).get("workers", []))
