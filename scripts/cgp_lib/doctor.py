"""`cgp doctor` (is the installation healthy?) and `cgp gc` (delete what dead sessions and finished stories left behind)."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from .consts import AUTO_FIELD, PRIORITY_FIELD, HOME, LOCK_STALE_SECONDS, LOCKS, STORY_OPTION, TEXT_FIELDS, WAITING_FIELD
from .util import die, out, strip_id
from .gh import gh, gql
from .store import list_boards, load_board, load_json, save_board
from .board import fetch_items, migrate_poll_default, fetch_project, fields_by_name, parse_board_url, parse_item
from .gitutil import git
from .gitwt import is_dirty
from .notify import command_problem
from .daemon import FLAGS as DAEMON_FLAGS
from .util import printable

# Permission rules docs/safety.md recommends for sessions that run the loop; doctor only reports whether they are present.
RECOMMENDED_DENY = ("Bash(gh pr merge:*)", "Bash(gh auth token:*)", "Bash(gh api graphql:*)")


def live_sessions():
    """Session ids that hold a board's lock and are still alive."""
    held = (load_json(f, None) for f in glob.glob(os.path.join(LOCKS, "*.json")))
    return {strip_id(lk["session"]) for lk in held if lk and time.time() - lk.get("at", 0) < LOCK_STALE_SECONDS}


def session_files(days):
    """State and stop files of sessions that are not live and have been idle for `days`, plus the pre-multi-session state.json."""
    live, cutoff, dead = live_sessions(), time.time() - days * 86400, []
    for f in glob.glob(os.path.join(HOME, "state-*.json")) + glob.glob(os.path.join(HOME, "stop-*")):
        m = re.match(r"(?:state|stop)-(.+?)(?:\.json)?$", os.path.basename(f))
        if m and m.group(1) not in live and os.path.getmtime(f) < cutoff:
            dead.append(f)
    legacy = os.path.join(HOME, "state.json")
    return dead + ([legacy] if os.path.exists(legacy) else [])


def finished_stories(c):
    """(owner, repo, number) -> item, for every closed issue or Done story on a board."""
    found = {}
    for raw in fetch_items(c["board"]["id"]):
        it = parse_item(raw, c)
        if it["kind"] == "issue" and (it["closed"] or it["column"] == "done"):
            owner, name = it["issueRepo"].split("/")
            found[(owner, name, str(it["number"]))] = it
    return found


def stale_worktrees(done):
    root = os.path.join(HOME, "worktrees")
    return [p for p in glob.glob(os.path.join(root, "*", "*", "*"))
            if tuple(os.path.relpath(p, root).split(os.sep)) in done and not is_dirty(p)]  # uncommitted work is kept


def cmd_gc(a):
    removed = [f for f in list_stale_locks()] + session_files(a.days)
    worktrees = []
    for key in list_boards():
        c = load_board(key)
        try:
            done = finished_stories(c)
        except SystemExit:  # no network: leave worktrees alone
            continue
        for wt in stale_worktrees(done):
            owner, name, number = os.path.relpath(wt, os.path.join(HOME, "worktrees")).split(os.sep)
            path = c["repos"].get(f"{owner}/{name}")
            if not a.dry_run:
                if path and os.path.isdir(path):
                    git(path, "worktree", "remove", "--force", wt, check=False)
                    git(path, "branch", "-D", f"cgp/{number}", check=False)
                shutil.rmtree(wt, ignore_errors=True)
            worktrees.append(wt)
            plan = os.path.join(HOME, "plans", f"{owner}-{name}-{number}.html")
            if os.path.exists(plan):
                removed.append(plan)
    if not a.dry_run:
        for f in removed:
            if os.path.exists(f):
                os.remove(f)
    out({"dryRun": a.dry_run, "removedFiles": removed, "removedWorktrees": worktrees})


def list_stale_locks():
    return [f for f in glob.glob(os.path.join(LOCKS, "*.json"))
            if time.time() - (load_json(f, {}) or {}).get("at", 0) >= LOCK_STALE_SECONDS]


def settings_denies():
    """Deny rules found in the user's and the current project's Claude Code settings files."""
    rules = set()
    for f in (os.path.expanduser("~/.claude/settings.json"), os.path.join(os.getcwd(), ".claude", "settings.json"),
              os.path.join(os.getcwd(), ".claude", "settings.local.json")):
        try:
            with open(f) as fh:
                rules |= set(json.load(fh).get("permissions", {}).get("deny", []))
        except (OSError, ValueError):
            pass
    return rules


def cmd_doctor(a):
    results = []

    def check(name, ok, detail="", warn=False, info=False):
        """`detail` is the fix to apply, shown only when the check fails (or always, with info, for facts like a version)."""
        results.append({"check": name, "ok": ok, "warn": warn and not ok, "detail": detail if info or not ok else ""})
        return ok

    check("python", sys.version_info >= (3, 8), sys.version.split()[0], info=True)
    v = subprocess.run(["gh", "--version"], capture_output=True, text=True) if shutil.which("gh") else None
    if not check("gh installed", bool(v and v.returncode == 0), (v.stdout.splitlines() or [""])[0] if v else "install https://cli.github.com", info=True):
        return report(results)
    p = gh("auth", "status", check=False)
    text = p.stdout + p.stderr
    scopes = re.search(r"Token scopes:(.*)", text)
    check("gh authenticated", p.returncode == 0, "run: gh auth login" if p.returncode else "")
    check("token has the project scope", not scopes or "'project'" in scopes.group(1), "run: gh auth refresh -s project")
    keys = list_boards()
    if not check("a board is set up", bool(keys), "run /cgp:setup <board-url>"):
        return report(results)
    boards = [load_board(k) for k in keys]
    if a.board:
        _, owner, number = parse_board_url(a.board)
        boards = [c for c in boards if c["board"]["number"] == number and c["board"]["owner"].lower() == owner.lower()]
        if not boards:
            die("that board is not set up yet")
    seen = {}
    for c in [load_board(k) for k in keys]:
        for r in c["repos"]:
            seen.setdefault(r.lower(), []).append(c["board"]["title"])
    dupes = {r: t for r, t in seen.items() if len(t) > 1}
    check("each repo is on one board", not dupes,
          "; ".join(f"{r} is on {', '.join(t)}" for r, t in dupes.items()) + ": remove it from all but one board", warn=True)
    for c in boards:
        title = c["board"]["title"]
        b = c["board"]
        if migrate_poll_default(c["settings"]):
            save_board(c)
            check(f"{title}: pollSeconds", True, "moved from the old default of 30 s to 15 s (cgp config pollSeconds N sets your own)", info=True)
        try:
            proj = fetch_project(b["kind"], b["owner"], b["number"])
        except SystemExit:
            check(f"{title}: reachable", False, "the project is not accessible with this token")
            continue
        fields = fields_by_name(proj)
        missing = [n for n in (WAITING_FIELD, *TEXT_FIELDS.values()) if n not in fields]
        check(f"{title}: Auto Approve and Priority fields", {AUTO_FIELD, PRIORITY_FIELD} <= set(fields), "run /cgp:setup again", warn=True)
        check(f"{title}: fields", not missing, f"missing {missing}: run /cgp:setup again" if missing else "")
        waiting = fields.get(WAITING_FIELD, {})
        check(f"{title}: Waiting On has 'You' and '{STORY_OPTION}'",
              {"You", STORY_OPTION} <= {o["name"] for o in waiting.get("options", [])}, "run /cgp:setup again", warn=True)
        if c["settings"]["nativeDependencies"]:
            try:
                have = "blockedBy" in {f["name"] for f in gql('query{ __type(name:"Issue"){ fields{ name } } }')["__type"]["fields"]}
            except SystemExit:
                have = False
            check(f"{title}: GitHub issue dependencies", have,
                  "this GitHub has no blockedBy on issues, so only cgp's own blocks order stories: cgp config nativeDependencies off", warn=True)
        check(f"{title}: auto-approval policy", True, f"autoApprove {c['settings']['autoApprove']}; files {', '.join(c['settings']['autoApproveFiles']) or 'none'}", info=True)
        cmd = (c["settings"].get("notifyCommand") or "").strip()
        problem = command_problem(cmd) if cmd else None
        check(f"{title}: notifyCommand", not problem, f"it will not run: {problem}", warn=True)
        for repo, path in c["repos"].items():
            ok = bool(path) and os.path.isdir(path)
            check(f"{title}: clone of {repo}", ok, path or f"unknown: cgp repo-path {repo} <path>")
            if ok:
                head = git(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
                check(f"{title}: default branch of {repo}", bool(head), head or "run: git remote set-head origin --auto", warn=True)
    try:
        claude = subprocess.run(["claude", "--help"], capture_output=True, text=True, timeout=10).stdout if shutil.which("claude") else None
    except (subprocess.TimeoutExpired, OSError):
        claude = None
    unknown = [f for f in DAEMON_FLAGS if f not in (claude or "")]
    check("claude for cgp daemon", claude is not None and not unknown,
          "cgp daemon needs the claude CLI on PATH" if claude is None else f"this claude does not list {', '.join(unknown)}: update it before running cgp daemon",
          warn=True)
    lock = list_stale_locks()
    check("no stale locks", not lock, f"{len(lock)} abandoned lock(s): cgp gc", warn=True)
    old = session_files(7)
    check("no leftover session files", not old, f"{len(old)} file(s) from dead sessions: cgp gc", warn=True)
    denied = settings_denies()
    lacking = [r for r in RECOMMENDED_DENY if r not in denied]
    check("recommended permission denies", not lacking,
          f"not denied in Claude Code settings: {', '.join(lacking)} (see docs/safety.md)", warn=True)
    return report(results)


def report(results):
    for r in results:
        print(printable(f"{'✅' if r['ok'] else '⚠️ ' if r['warn'] else '❌'} {r['check']}{'  ' + r['detail'] if r['detail'] else ''}"))
    if any(not r["ok"] and not r["warn"] for r in results):
        sys.exit(1)
