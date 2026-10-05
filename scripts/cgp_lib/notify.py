"""Run the user's `notifyCommand` setting when something needs them: a story starts waiting on them, enters a review column,
is Done, or stalls. The story's text goes in environment variables (CGP_EVENT, CGP_TITLE, CGP_URL, CGP_BOARD), never on a command line."""
import os
import re
import shlex
import subprocess
from .store import update_data

REVIEW = ("plan_review", "pr_review")


def fire(c, event, title, url):
    cmd = (c["settings"].get("notifyCommand") or "").strip()
    if not cmd:
        return
    env = {**os.environ, "CGP_EVENT": event, "CGP_TITLE": re.sub(r"[\x00-\x1f\x7f]", "", title or ""), "CGP_URL": url or "",
           "CGP_BOARD": c["board"]["title"]}
    try:
        subprocess.run(shlex.split(cmd), env=env, timeout=10, stdin=subprocess.DEVNULL, capture_output=True)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass  # a broken notifier must never stop the loop


def check(c, items, stalled):
    """Fire events for what changed since the last snapshot. The first snapshot after the setting appears only records the
    current state, so enabling it does not announce the whole board."""
    events, started = [], {s["item"]: s for s in stalled}

    def upd(d):
        seen, first = d.setdefault("notified", {}), "notified" not in d or not d["notified"]
        for it in items:
            was = seen.get(it["item"], {})
            now = {"column": it["column"], "waiting": bool(it["waiting"]), "stalled": was.get("stalled")}
            if not first:
                if it["waiting"] and not was.get("waiting"):
                    events.append(("waiting", it))
                if it["column"] in REVIEW and was.get("column") != it["column"]:
                    events.append(("review", it))
                if it["column"] == "done" and was.get("column") not in (None, "done"):
                    events.append(("done", it))
                if it["item"] in started and was.get("stalled") != started[it["item"]].get("startedAt"):
                    events.append(("stalled", it))
            if it["item"] in started:
                now["stalled"] = started[it["item"]].get("startedAt")
            seen[it["item"]] = now
        for k in [k for k in seen if k not in {i["item"] for i in items}]:
            del seen[k]
    update_data(upd)
    for event, it in events:
        fire(c, event, it["title"], it.get("url"))
