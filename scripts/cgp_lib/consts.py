"""Paths, column layout, phases and default settings shared by every module."""
import os
import re


HOME = os.environ.get("CGP_HOME") or os.path.expanduser("~/.config/claude-github-project")
BOARDS = os.path.join(HOME, "boards")  # <project id>.json (config), <project id>.data.json (blocks, touches, cursors)
PATHS = os.path.join(HOME, "paths.json")  # repo -> local clone, shared by all boards
LOCKS = os.path.join(HOME, "locks")  # one per board: which session is running its loop
LOCK_STALE_SECONDS = 30 * 60
MARK = "<!-- cgp -->"
QMARK = "<!-- cgp:question -->"
# key, name, emoji, color. plan_review and pr_review are the user's columns (the gates in cmd_move and cmd_merge are built on them).
COLUMNS = [
    ("todo", "Todo", "🆕", "BLUE"),
    ("plan", "Plan", "🧠", "YELLOW"),
    ("plan_review", "Plan Review", "🙋", "PURPLE"),
    ("plan_approved", "Plan Approved", "✅", "BLUE"),
    ("implement", "Implement", "🔨", "RED"),
    ("pr_review", "PR Review", "🚦", "PURPLE"),
    ("pr_approved", "PR Approved", "🚀", "BLUE"),
    ("done", "Done", "🎉", "GREEN"),
]
# Pipeline order
ALL_KEYS = [k for k, _, _, _ in COLUMNS]
# board config layout: 3 = the columns above. 2 named the user's columns plan_approval / pr_approval (load_board renames them);
# 1 was the ten-column layout with agent-side review columns, which is no longer migrated.
SCHEMA = 3
KEY_RENAMES = {"plan_approval": "plan_review", "pr_approval": "pr_review"}  # schema 2 -> 3
# What a worker is doing right now, shown in the mod (cgp worker phase)
PHASE_LABELS = {"planning": ("🧠", "planning"), "reviewing": ("🔍", "reviewing"), "revising": ("✏️", "revising"),
                "implementing": ("🔨", "implementing"), "fixing": ("🔧", "fixing review findings"), "ci": ("⏳", "waiting on CI"),
                "merging": ("🚀", "merging")}
PHASES = tuple(PHASE_LABELS)
# Later pipeline stages first, so stories finish sooner when capped.
# The phase a worker is in unless it says otherwise, so a worker that forgets `cgp worker phase` still shows something true.
# The CLI also infers later ones from what the worker does: publishing the plan (cgp set plan) or opening the PR (cgp set pr)
# means review is next, ci-wait is "ci" while it waits, merge and merge-wait are "merging".
DEFAULT_PHASE = {"todo": "planning", "plan": "planning", "plan_approved": "implementing", "implement": "implementing",
                 "pr_approved": "merging"}
ACTIONABLE = ["pr_approved", "plan_approved", "implement", "plan", "todo"]
TEXT_FIELDS = {"plan": "Plan", "pr": "PR", "preview": "Preview"}
WAITING_FIELD = "Waiting On"
AUTO_FIELD = "Auto Approve"  # single-select Plan / PR / Both, set by the user: the agent may then pass that human gate itself
AUTO_OPTIONS = ("Plan", "PR", "Both")
SKIP = "skip"  # a Plan field of "Skip" (set by the user on a Todo story) means the story is implemented without a plan
PRIORITY_FIELD = "Priority"  # single-select; stories are dispatched High before Medium before Low before unset, Hold is never dispatched
PRIORITY_OPTIONS = (("High", "RED"), ("Medium", "YELLOW"), ("Low", "BLUE"), ("Hold", "GRAY"))
STORY_OPTION = "Another story"  # Waiting On value for a story queued behind a blocker; "You" is the user
# concurrency 0 = no cap; sharedFiles: fnmatch globs for files many stories edit (registries, lockfiles): overlaps on them never block
# remoteControl 1: /cgp:run turns on Remote Control for its session (desktop app only), so the loop can be followed from claude.ai or the phone
# previewProvider: netlify | vercel | cloudflare | deployments (a repo's .cgp.json "preview" overrides it)
# draftPRs 1: workers open PRs as drafts and `cgp move ... pr_review` marks them ready
# plannerModel / reviewerModel / implementerModel: sonnet | opus | haiku | fable, or low:haiku,medium:sonnet,high:opus (see models.py)
# notifyCommand: a command run when a story starts waiting on you, enters a review column, is Done or stalls (see notify.py)
# maxWorkerMinutes: a worker running longer is reported as stalled by `cgp list` (0 = never), so the loop can release its story
# guardFiles: fnmatch globs (tried on the path and on its file name) for files an agent may only change when the approved plan lists them
DEFAULTS = {"concurrency": 0, "pollSeconds": 30, "remoteControl": 1, "notifyCommand": "", "previewProvider": "netlify", "draftPRs": 0,
            "plannerModel": "", "reviewerModel": "", "implementerModel": "", "maxWorkerMinutes": 240,
            "guardFiles": [".github/*", "CODEOWNERS", "*/CODEOWNERS", ".husky/*", ".pre-commit-config.yaml", ".npmrc", ".yarnrc*",
                           "Makefile", "Dockerfile*", ".gitmodules", ".claude/*", ".mcp.json"],
            "sharedFiles": ["*package-lock.json", "*yarn.lock", "*pnpm-lock.yaml", "*.schema.json", "*locales/*",
                            "*__snapshots__/*", "*.md"]}
STRING_SETTINGS = ("notifyCommand", "previewProvider", "plannerModel", "reviewerModel", "implementerModel")
PR_URL = re.compile(r"^https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/(\d+)/?$")
# name, layout, visible fields, filter. The same three tabs as the 18xx-maker board. The API can set a view's name, layout,
# visible fields and filter, but not its grouping or sort, so "Board" and "Approvals" are columns by Status (GitHub's default)
# and group-by Repository is left for the user to turn on.
VIEWS = [
    ("Tasks", "TABLE_LAYOUT", ["Title", "Status", "Repository", WAITING_FIELD, "Plan", "PR", "Preview", AUTO_FIELD, PRIORITY_FIELD], ""),
    ("Board", "BOARD_LAYOUT", ["Title", "Assignees", "Status"], ""),
    ("Approvals", "BOARD_LAYOUT", ["Title", "Status"],
     "status:" + ",".join(f'"{e} {n}"' for k, n, e, _ in COLUMNS if k in ("plan_review", "pr_review"))),
]
NETLIFY_BOT = "netlify[bot]"
LINKS_START, LINKS_END = "<!-- cgp:links -->", "<!-- /cgp:links -->"

# What the mod draws, written into every session state file so the band has no copy of these tables to keep in step.
META = {"emoji": {k: e for k, _, e, _ in COLUMNS}, "phases": {k: list(v) for k, v in PHASE_LABELS.items()}}
