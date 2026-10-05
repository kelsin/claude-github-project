"""A per-board log of what happened to each story (moves, worker runs, questions, merges), for `cgp replay` and `cgp report`."""
import os
from datetime import datetime, timezone
from statistics import median
from .consts import BOARDS
from .util import age_seconds, die, now_iso, out, safe
from .store import board_key, cfg, load_json, locked, save_json

PER_STORY, STORIES = 300, 1000  # the oldest events and the least recently active stories are dropped beyond these


def history_path():
    return os.path.join(BOARDS, f"{safe(board_key())}.history.json")


def record(item, kind, title=None, **fields):
    """Append one event to a story's history. Never raises: the log must not get in the way of the work."""
    try:
        with locked():
            os.makedirs(BOARDS, mode=0o700, exist_ok=True)
            h = load_json(history_path(), {})
            ev = {"at": now_iso(), "kind": kind, **{k: v for k, v in fields.items() if v is not None}}
            story = h.setdefault(item, {"title": title or "", "events": []})
            if title:
                story["title"] = title
            story.setdefault("startedAt", story["events"][0]["at"] if story["events"] else ev["at"])  # survives trimming
            story["events"] = (story["events"] + [ev])[-PER_STORY:]
            if len(h) > STORIES:
                for k in sorted(h, key=lambda k: h[k]["events"][-1]["at"])[: len(h) - STORIES]:
                    del h[k]
            save_json(history_path(), h)
    except (OSError, ValueError, SystemExit):
        pass


def describe(ev):
    rest = " ".join(f"{k}={v}" for k, v in ev.items() if k not in ("at", "kind"))
    return f"{ev['at']}  {ev['kind']:<13} {rest}".rstrip()


def cmd_replay(a):
    cfg()
    story = load_json(history_path(), {}).get(a.item)
    if not story:
        die("no history for that story yet")
    if a.json:
        out(story)
        return
    print(story["title"] or a.item)
    for ev in story["events"]:
        print("  " + describe(ev))


def story_summary(story):
    """Cycle time (first event to Done), worker runs and review rounds of one story; None when it is not Done."""
    ev = story["events"]
    last = next((e for e in reversed(ev) if e["kind"] == "move"), None)
    done = last if last and last.get("to") == "done" else None  # a story moved out of Done again is not done
    return {"title": story["title"], "runs": sum(1 for e in ev if e["kind"] == "worker-start"),
            "reviews": sum(1 for e in ev if e["kind"] == "move" and e.get("to") in ("plan_review", "pr_review")),
            "questions": sum(1 for e in ev if e["kind"] == "ask"), "doneAt": done["at"] if done else None,
            "hours": round((_ts(done["at"]) - _ts(story.get("startedAt") or ev[0]["at"])) / 3600, 1) if done else None}


def _ts(iso):
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def cmd_report(a):
    cfg()
    rows = [story_summary(s) for s in load_json(history_path(), {}).values() if s["events"]]
    done = [r for r in rows if r["doneAt"] and age_seconds(r["doneAt"]) <= a.days * 86400]
    hours = [r["hours"] for r in done]
    res = {"days": a.days, "done": len(done), "medianHours": median(hours) if hours else None,
           "inProgress": sum(1 for r in rows if not r["doneAt"]), "stories": sorted(done, key=lambda r: r["doneAt"])}
    if a.json:
        out(res)
        return
    print(f"Last {a.days} days: {res['done']} done, median cycle {res['medianHours']} h, {res['inProgress']} in progress")
    for r in res["stories"]:
        print(f"  {r['hours']:>6} h  {r['runs']} runs  {r['reviews']} reviews  {r['questions']} questions  {r['title']}")
