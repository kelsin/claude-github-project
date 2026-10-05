"""Small helpers with no state: errors, JSON output, name and path normalising."""
import argparse
import contextlib
import io
import json
import re
import sys
import time
from datetime import datetime, timezone


def die(msg, code=1):
    print(f"cgp: {msg}", file=sys.stderr)
    sys.exit(code)


def out(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def norm(s):
    return re.sub(r"^[^A-Za-z0-9]+", "", s or "").strip().lower()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_seconds(iso):
    """Seconds since an ISO timestamp written by now_iso(); 0 when it is missing or unreadable."""
    try:
        return datetime.now(timezone.utc).timestamp() - datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0


def safe(key):
    """A board or session key as a file name."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", key)


def strip_id(key):
    """A session id with everything outside [A-Za-z0-9_-] removed (the same sanitising as the mod)."""
    return re.sub(r"[^A-Za-z0-9_-]", "", key or "")


def split_repo(repo):
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo or ""):
        die(f"repo must be <owner>/<name>, got {repo!r}")
    return repo.split("/")


def norm_path(path):
    return re.sub(r"^(\./)+", "", path.strip())


def covers(a, b):
    """True when path a (file, or directory ending in /) contains or equals path b."""
    return a == b or (a.endswith("/") and b.startswith(a))


def call(fn, **kw):
    """Run another command's handler in-process and return the JSON it prints; {"error": ...} when it fails or prints none.
    Lets one command compose others without paying a Python start-up (and a board reload) for each."""
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            fn(argparse.Namespace(**kw))
    except SystemExit:
        pass
    try:
        return json.loads(buf.getvalue())
    except ValueError:
        return {"error": (err.getvalue() or buf.getvalue()).strip()}


class Poll:
    """The deadline and pause of a polling loop: `if not poll.wait(): return <pending result>` ends a loop that has run out of time,
    otherwise sleeps one interval and goes on."""

    def __init__(self, timeout, interval):
        self.deadline, self.interval = time.time() + timeout, interval

    def expired(self):
        return time.time() >= self.deadline

    def wait(self):
        if self.expired():
            return False
        time.sleep(self.interval)
        return True
