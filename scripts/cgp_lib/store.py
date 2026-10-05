"""Everything under ~/.config/claude-github-project: board configs, per-board data, session state, locks."""
import fcntl
import json
import os
import time
from .consts import BOARDS, DEFAULTS, HOME, LEGACY_CONFIG, LOCKS, LOCK_STALE_SECONDS, PATHS
from .util import die, safe, strip_id


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def ensure_home():
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    if os.stat(HOME).st_mode & 0o777 != 0o700:
        os.chmod(HOME, 0o700)


def save_json(path, obj):
    ensure_home()
    tmp = f"{path}.{os.getpid()}.tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


class locked:
    """The one cross-process lock for state files. Re-entrant within a process (flock on a second descriptor would deadlock)."""
    depth = 0
    f = None

    def __enter__(self):
        if locked.depth == 0:
            ensure_home()
            locked.f = open(os.path.join(HOME, ".lock"), "w")
            fcntl.flock(locked.f, fcntl.LOCK_EX)
        locked.depth += 1

    def __exit__(self, *a):
        locked.depth -= 1
        if locked.depth == 0:
            fcntl.flock(locked.f, fcntl.LOCK_UN)
            locked.f.close()


def sid():
    """This session's id (exported by the mod as CGP_SESSION); 'default' when run outside a session."""
    return strip_id(os.environ.get("CGP_SESSION")) or "default"


def state_path():
    return os.path.join(HOME, f"state-{sid()}.json")  # what this session's UI shows: workers, counts, waiting


def stop_path():
    return os.path.join(HOME, f"stop-{sid()}")  # "1" = finish the current cycle, then stop (written by `cgp stop` or the mod button)


def board_file(key):
    return os.path.join(BOARDS, f"{safe(key)}.json")


def list_boards():
    ensure_home()
    keys = [f[:-5] for f in sorted(os.listdir(BOARDS))
            if f.endswith(".json") and not f.endswith(".data.json")] if os.path.isdir(BOARDS) else []
    legacy = load_json(LEGACY_CONFIG, None)
    if legacy and legacy.get("board", {}).get("id") and safe(legacy["board"]["id"]) not in keys:
        save_board(legacy)  # migrate the single-board config of earlier versions
        keys.append(safe(legacy["board"]["id"]))
    return keys


def load_board(key):
    raw = load_json(board_file(key), None)
    if not raw:
        die("unknown board; run /cgp:setup <board-url>")
    paths = load_json(PATHS, {})
    raw["repos"] = {r: paths.get(r) for r in raw["repos"]}
    raw["settings"] = {**DEFAULTS, **raw.get("settings", {})}  # configs saved before a setting existed lack it
    return raw


def save_board(c):
    with locked():  # paths.json is shared by every board: read-modify-write under the lock
        os.makedirs(BOARDS, mode=0o700, exist_ok=True)
        paths = load_json(PATHS, {})
        paths.update({r: p for r, p in c["repos"].items() if p})
        save_json(PATHS, paths)
        save_json(board_file(c["board"]["id"]), {**c, "repos": {r: None for r in c["repos"]}})


def board_key():
    key = load_json(state_path(), {}).get("boardKey")
    if key and os.path.exists(board_file(key)):
        return key
    keys = list_boards()
    if len(keys) == 1:
        return keys[0]
    if not keys:
        die("no board configured; run /cgp:setup <board-url> first")
    die("this session is not bound to a board; run: cgp use <board-url>", code=6)


def cfg():
    return load_board(board_key())


def data_path():
    return os.path.join(BOARDS, f"{safe(board_key())}.data.json")


def load_data():
    return load_json(data_path(), {})


def update_data(fn):
    """Per-board data that outlives sessions: blocks, touches, feedback cursors, answered flags."""
    with locked():
        os.makedirs(BOARDS, mode=0o700, exist_ok=True)
        d = load_json(data_path(), {})
        fn(d)
        save_json(data_path(), d)


def lock_file(key):
    return os.path.join(LOCKS, f"{safe(key)}.json")


def lock_holder(key):
    lk = load_json(lock_file(key), None)
    if lk and lk.get("session") != sid() and time.time() - lk.get("at", 0) < LOCK_STALE_SECONDS:
        return lk
    return None


def touch_lock(key):
    lk = load_json(lock_file(key), None)
    if lk and lk.get("session") == sid():
        lk["at"] = time.time()
        save_json(lock_file(key), lk)


def update_state(fn):
    """This session's display state (workers, counts, waiting). Any write is also the loop's heartbeat."""
    with locked():
        st = load_json(state_path(), {"workers": [], "counts": {}, "waiting": []})
        fn(st)
        save_json(state_path(), st)
        if st.get("boardKey"):
            touch_lock(st["boardKey"])


def stop_requested():
    try:
        with open(stop_path()) as f:
            return f.read().strip() == "1"
    except FileNotFoundError:
        return False
