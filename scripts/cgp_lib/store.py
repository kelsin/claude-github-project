"""Everything under ~/.config/claude-github-project: board configs, per-board data, session state, locks."""
import fcntl
import json
import os
import time
from .consts import BOARDS, DEFAULTS, HOME, KEY_RENAMES, LOCKS, META, LOCK_STALE_SECONDS, PATHS, SCHEMA
from .gitutil import cwd_repo
from .util import die, safe, strip_id


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError:
        die(f"{path} is corrupt (not valid JSON); fix or delete it")


def ensure_home():
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    if os.stat(HOME).st_mode & 0o777 != 0o700:
        os.chmod(HOME, 0o700)


def save_json(path, obj):
    ensure_home()
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


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


_cwd_board = []


def boards_of_repo(repo):
    """Keys of the boards whose repos include this one (a repo belongs to one board; legacy configs may list it on several)."""
    return [k for k in list_boards() if repo.lower() in {r.lower() for r in (load_json(board_file(k), None) or {}).get("repos", {})}]


def boards_of_cwd():
    if not _cwd_board:
        repo = cwd_repo()
        _cwd_board.append(boards_of_repo(repo) if repo else [])
    return _cwd_board[0]


def board_for_cwd():
    """Key of the board the current directory's repo is on; None when it is on none, or (legacy configs) on several."""
    keys = boards_of_cwd()
    return keys[0] if len(keys) == 1 else None


def sid():
    """This session's id (exported by the mod as CGP_SESSION). Outside a session it is 'default', or 'default-<board>' in a
    board's repo, so bare terminals on different boards do not share state, locks or stop files."""
    s = strip_id(os.environ.get("CGP_SESSION"))
    if s:
        return s
    key = board_for_cwd()
    return f"default-{safe(key)}" if key else "default"


def state_path():
    return os.path.join(HOME, f"state-{sid()}.json")  # what this session's UI shows: workers, counts, waiting


def stop_path():
    return os.path.join(HOME, f"stop-{sid()}")  # "1" = finish the current cycle, then stop (written by `cgp stop` or the mod button)


def board_file(key):
    return os.path.join(BOARDS, f"{safe(key)}.json")


def list_boards():
    ensure_home()
    keys = [f[:-5] for f in sorted(os.listdir(BOARDS))
            if f.endswith(".json") and not f.endswith((".data.json", ".history.json"))] if os.path.isdir(BOARDS) else []
    return keys


def load_board(key):
    raw = load_json(board_file(key), None)
    if not raw:
        die("unknown board; run /cgp:setup <board-url>")
    if raw.get("schema", 1) < 2:
        die(f"{raw['board']['title']} still has the ten-column layout, which this version no longer migrates. Run /cgp:run once with "
            "cgp 0.1.x (the last version that does), or recreate its Status options with /cgp:setup on a copy; see docs/migration.md")
    if raw["schema"] < SCHEMA:  # 2 -> 3: the user's columns were called plan_approval / pr_approval
        opts = raw["fields"]["status"]["options"]
        raw["fields"]["status"]["options"] = {KEY_RENAMES.get(k, k): v for k, v in opts.items()}
        raw["schema"] = SCHEMA
        save_json(board_file(key), raw)
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


def update_board(fn):
    """Change this board's config under the lock, on the file's current contents, so concurrent edits do not overwrite each other."""
    with locked():
        c = cfg()
        fn(c)
        save_board(c)
        return c


def env_board():
    """The board named by $CGP_BOARD (its key or its URL), set by `cgp daemon` for the sessions it starts; None when unset or unknown."""
    want = (os.environ.get("CGP_BOARD") or "").strip().rstrip("/")
    if not want:
        return None
    for k in list_boards():
        if k == want or (load_json(board_file(k), {}).get("board", {}).get("url") or "").rstrip("/") == want:
            return k
    return None


def board_key():
    """The board for this command: $CGP_BOARD, else the session's bound board (cgp use), else the one the current repo is on, else the only board."""
    key = env_board() or load_json(state_path(), {}).get("boardKey")
    if key and os.path.exists(board_file(key)):
        return key
    keys = list_boards()
    if len(keys) == 1:
        return keys[0]
    if not keys:
        die("no board configured; run /cgp:setup <board-url> first")
    here = board_for_cwd()
    if here:
        return here
    if len(boards_of_cwd()) > 1:
        die(f"{cwd_repo()} is on several boards; a repo should belong to one. Remove it from the others, "
            "or bind this session with: cgp use <board-url>", code=6)
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
        st["meta"] = META
        save_json(state_path(), st)
        if st.get("boardKey"):
            touch_lock(st["boardKey"])


def stop_requested():
    try:
        with open(stop_path()) as f:
            return f.read().strip() == "1"
    except FileNotFoundError:
        return False
