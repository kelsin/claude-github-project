"""Run the user's `notifyCommand` setting when something needs them: a story starts waiting on them, enters a review column,
is Done, or stalls. The story's text goes in environment variables (CGP_EVENT, CGP_TITLE, CGP_URL, CGP_BOARD), never on a command line."""
import os
import re
import shlex
import subprocess
from .consts import HOME
from .store import update_data
from .util import IS_WINDOWS, printable

REVIEW = ("plan_review", "pr_review")


SECRET_ENV = re.compile(r"^(GH_|GITHUB_|GIT_)|_TOKEN$", re.I)
WORKTREES = os.path.join(HOME, "worktrees")


def command_problem(cmd):
    """Why `notifyCommand` may not run (None when it may): the program must be an absolute path to an executable file that only
    this user can write, outside worktrees, /tmp and the cgp home, because a worker can write files in all of those."""
    if IS_WINDOWS:
        return "notifyCommand is not supported on Windows"
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return "it is not a valid command line"
    if not argv or not os.path.isabs(argv[0]):
        return "the program must be an absolute path"
    path = os.path.realpath(argv[0])
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        return f"{argv[0]} is not an executable file"
    st = os.stat(path)
    if st.st_uid != os.getuid():
        return f"{argv[0]} is not owned by you"
    if st.st_mode & 0o022:
        return f"{argv[0]} is writable by group or others"
    for bad in (WORKTREES, HOME, "/tmp", "/private/tmp", "/var/tmp"):
        bad = os.path.realpath(bad)
        if path == bad or path.startswith(bad + os.sep):
            return f"{argv[0]} is under {bad}"
    return None


def fire(c, event, title, url):
    cmd = (c["settings"].get("notifyCommand") or "").strip()
    if not cmd or command_problem(cmd):
        return
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    env.update(CGP_EVENT=event, CGP_TITLE=printable(title), CGP_URL=url or "", CGP_BOARD=c["board"]["title"])
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
