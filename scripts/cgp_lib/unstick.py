"""`cgp unstick <item>`: clear one story's operational state so the loop starts it fresh. For the user, never for an agent."""
import glob
import json
import os
from .consts import HOME, STORY_STATE
from .util import die, out, pid_alive, ps_field
from .store import cfg, load_data, locked, save_json, update_data
from .board import clear_field, get_item, parse_pr_ref
from .pr import cancel_auto_merge
from .session import kill_orphan


def live_worker(w):
    """A worker row whose process is still the one that was started (a recorded start time must still match)."""
    pid = w.get("pid")
    return isinstance(pid, int) and pid > 1 and pid_alive(pid) and (not w.get("start") or ps_field(pid, "lstart") == w["start"])


def worker_rows(item):
    """(state file, its rows) for every session that has a worker row for this story."""
    found = []
    for f in sorted(glob.glob(os.path.join(HOME, "state-*.json"))):
        try:
            with open(f) as fh:
                rows = [w for w in json.load(fh).get("workers", []) if isinstance(w, dict) and w.get("item") == item]
        except (OSError, ValueError, AttributeError):
            continue
        if rows:
            found.append((f, rows))
    return found


def story_state(d, item):
    """The entries of the data file that belong to this story and are operational (STORY_STATE), as {key: what}."""
    found = {}
    for key, kind in STORY_STATE.items():
        have = d.get(key) or {}
        mine = [item] if kind == "list" and item in have else [k for k in have if k.split("|")[0] == item] if kind == "pipe" else []
        if mine:
            found[key] = mine
    return found


def clear_story_state(d, item):
    for key, mine in story_state(d, item).items():
        if STORY_STATE[key] == "list":
            d[key] = [i for i in d[key] if i != item]
        else:
            for k in mine:
                del d[key][k]


def cmd_unstick(a):
    """Clear a story's worker row, strikes, answered flag and Waiting On, and disarm auto-merge on its PR. Never touches its
    column, plan, PR, touches or approved touches (STORY_KEPT). Refuses while its worker still runs, unless --kill."""
    if os.environ.get("CGP_DAEMON"):
        die("unstick is only for you: a worker may not clear a story's state")
    c = cfg()
    it = get_item(c, a.item)
    rows = worker_rows(a.item)
    live = [w for _, ws in rows for w in ws if live_worker(w)]
    ref = parse_pr_ref(c, it["pr"])
    plan = {"item": a.item, "dryRun": a.dry_run, "workers": [w.get("pid") or "unregistered" for _, ws in rows for w in ws],
            "data": story_state(load_data(), a.item), "waitingOn": bool(it["waiting"]),
            "cancelAutoMerge": f"{ref[0]}#{ref[1]}" if ref else None}
    if a.dry_run:
        out({**plan, "stillRunning": [w["pid"] for w in live]})
        return
    if live and not a.kill:
        die(f"its worker (pid {', '.join(str(w['pid']) for w in live)}) is still running; stop it, or pass --kill")
    for w in live:
        if not kill_orphan(w):
            die(f"could not stop its worker (pid {w['pid']})")
    with locked():
        for f, _ in rows:
            with open(f) as fh:
                st = json.load(fh)
            st["workers"] = [w for w in st["workers"] if not (isinstance(w, dict) and w.get("item") == a.item)]
            save_json(f, st)
        update_data(lambda d: clear_story_state(d, a.item))
    if it["waiting"]:
        clear_field(c, a.item, c["fields"]["waiting"]["id"])
    if ref:
        plan["autoMergeOff"] = cancel_auto_merge(*ref)
    out(plan)
