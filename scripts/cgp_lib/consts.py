"""Paths, column layout, phases and default settings shared by every module."""
import os
import re


HOME = os.environ.get("CGP_HOME") or os.path.expanduser("~/.config/claude-github-project")
LEGACY_CONFIG = os.path.join(HOME, "config.json")  # single-board layout of earlier versions
BOARDS = os.path.join(HOME, "boards")  # <project id>.json (config), <project id>.data.json (blocks, touches, cursors)
PATHS = os.path.join(HOME, "paths.json")  # repo -> local clone, shared by all boards
LOCKS = os.path.join(HOME, "locks")  # one per board: which session is running its loop
LOCK_STALE_SECONDS = 30 * 60
MARK = "<!-- cgp -->"
QMARK = "<!-- cgp:question -->"
# key, name, emoji, color. The keys of the two human columns (plan_approval, pr_approval) predate their display names
# "Plan Review" / "PR Review"; they stay because the gates in cmd_move/cmd_merge, the prompts and old state files use them.
COLUMNS = [
    ("todo", "Todo", "🆕", "BLUE"),
    ("plan", "Plan", "🧠", "YELLOW"),
    ("plan_approval", "Plan Review", "🙋", "PURPLE"),
    ("plan_approved", "Plan Approved", "✅", "BLUE"),
    ("implement", "Implement", "🔨", "RED"),
    ("pr_approval", "PR Review", "🚦", "PURPLE"),
    ("pr_approved", "PR Approved", "🚀", "BLUE"),
    ("done", "Done", "🎉", "GREEN"),
]
# Pipeline order across every layout a board may still have: boards from before SCHEMA 2 also carry the agent-side
# plan_review / pr_review columns (see apply_columns), which are only ever present in their config, never created.
ALL_KEYS = ["todo", "plan", "plan_review", "plan_approval", "plan_approved", "implement", "pr_review", "pr_approval",
            "pr_approved", "done"]
SCHEMA = 2  # board config layout: 1 = ten columns with agent-side review columns, 2 = the columns above
# What a worker is doing right now, shown in the mod (cgp worker phase)
PHASES = ("planning", "reviewing", "revising", "implementing", "fixing", "ci", "merging")
# Later pipeline stages first, so stories finish sooner when capped.
# The phase a worker is in unless it says otherwise, so a worker that forgets `cgp worker phase` still shows something true.
# The CLI also infers later ones from what the worker does: publishing the plan (cgp set plan) or opening the PR (cgp set pr)
# means review is next, ci-wait is "ci" while it waits, merge and merge-wait are "merging".
DEFAULT_PHASE = {"todo": "planning", "plan": "planning", "plan_review": "planning", "plan_approved": "implementing",
                 "implement": "implementing", "pr_review": "implementing", "pr_approved": "merging"}
ACTIONABLE = ["pr_approved", "plan_approved", "pr_review", "implement", "plan_review", "plan", "todo"]  # the review ones: unmigrated boards only
TEXT_FIELDS = {"plan": "Plan", "pr": "PR", "preview": "Preview"}
WAITING_FIELD = "Waiting On"
AUTO_FIELD = "Auto Approve"  # single-select Plan / PR / Both, set by the user: the agent may then pass that human gate itself
AUTO_OPTIONS = ("Plan", "PR", "Both")
SKIP = "skip"  # a Plan field of "Skip" (set by the user on a Todo story) means the story is implemented without a plan
STORY_OPTION = "Another story"  # Waiting On value for a story queued behind a blocker; "You" is the user
# concurrency 0 = no cap; sharedFiles: fnmatch globs for files many stories edit (registries, lockfiles): overlaps on them never block
# remoteControl 1: /cgp:run turns on Remote Control for its session (desktop app only), so the loop can be followed from claude.ai or the phone
# autoMigrate 1: `cgp use` brings a board from an older column layout up to date (cgp migrate does it by hand)
# maxWorkerMinutes: a worker running longer is reported as stalled by `cgp list` (0 = never), so the loop can release its story
# guardFiles: fnmatch globs (tried on the path and on its file name) for files an agent may only change when the approved plan lists them
DEFAULTS = {"concurrency": 0, "pollSeconds": 30, "autoMigrate": 1, "remoteControl": 1, "maxWorkerMinutes": 240,
            "guardFiles": [".github/*", "CODEOWNERS", "*/CODEOWNERS", ".husky/*", ".pre-commit-config.yaml", ".npmrc", ".yarnrc*",
                           "Makefile", "Dockerfile*", ".gitmodules", ".claude/*", ".mcp.json"],
            "sharedFiles": ["*package-lock.json", "*yarn.lock", "*pnpm-lock.yaml", "*.schema.json", "*locales/*",
                            "*__snapshots__/*", "*.md"]}
PR_URL = re.compile(r"^https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/(\d+)/?$")
# name, layout, visible fields, filter. The same three tabs as the 18xx-maker board. The API can set a view's name, layout,
# visible fields and filter, but not its grouping or sort, so "Board" and "Approvals" are columns by Status (GitHub's default)
# and group-by Repository is left for the user to turn on.
VIEWS = [
    ("Tasks", "TABLE_LAYOUT", ["Title", "Status", "Repository", WAITING_FIELD, "Plan", "PR", "Preview", AUTO_FIELD], ""),
    ("Board", "BOARD_LAYOUT", ["Title", "Assignees", "Status"], ""),
    ("Approvals", "BOARD_LAYOUT", ["Title", "Status"],
     "status:" + ",".join(f'"{e} {n}"' for k, n, e, _ in COLUMNS if k in ("plan_approval", "pr_approval"))),
]
NETLIFY_BOT = "netlify[bot]"
LINKS_START, LINKS_END = "<!-- cgp:links -->", "<!-- /cgp:links -->"
