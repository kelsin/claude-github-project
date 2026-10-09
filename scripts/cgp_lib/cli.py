"""Command table and argument parsing: every `cgp <command>` is one row of COMMANDS."""
import argparse
import sys


from .board import (cmd_adopt, cmd_config, cmd_discover, cmd_repo_path, cmd_repos, cmd_setup)
from .epics import cmd_split
from .deferred import cmd_defer
from .doctor import cmd_doctor, cmd_gc
from .models import RATINGS, cmd_models
from .policy import cmd_rate
from .review import cmd_review
from .rules import cmd_rules
from .brief import cmd_brief
from .repoconf import cmd_repo_config
from .intake import cmd_add, cmd_import
from .gitwt import cmd_guard, cmd_sync, cmd_worktree, cmd_worktree_remove
from .pr import cmd_ci_triage, cmd_ci_wait, cmd_merge, cmd_merge_wait, cmd_pr_state
from .sched import cmd_block, cmd_list, cmd_overlap, cmd_status, cmd_touches, cmd_wait
from .unstick import cmd_unstick
from .resume import cmd_resume
from .stack import cmd_stack
from .session import cmd_release, cmd_session_title, cmd_stop, cmd_use, cmd_worker
from .story import (cmd_answers, cmd_approve, cmd_ask, cmd_comment, cmd_feedback, cmd_move, cmd_prepare, cmd_preview, cmd_set)
from .util import die

A = lambda *names, **kw: (names, kw)  # one add_argument call

# name, handler, help, arguments
COMMANDS = [
    ("setup", cmd_setup, "Configure a board (columns, fields, views) and link a repo", [
        A("url"), A("--repo"), A("--repo-path"),
        A("--dry-run", action="store_true", help="print what would change; touch nothing")]),
    ("use", cmd_use, "Bind this session to a board and claim its loop (exit 5: held by another session, 6: which board?)", [
        A("url", nargs="?"), A("--takeover", action="store_true")]),
    ("release", cmd_release, "Free this session's board lock", []),
    ("session-title", cmd_session_title, "UserPromptSubmit hook: name the session after the board", []),
    ("repo-path", cmd_repo_path, "Show or set the local clone of a linked repo", [A("repo", nargs="?"), A("path", nargs="?")]),
    ("discover", cmd_discover, "Find local clones of the board's repos under ~/src, ~/code, ...", [A("roots", nargs="*")]),
    ("config", cmd_config, "Show or set a board setting (concurrency, pollSeconds, remoteControl, sharedFiles, ...)", [
        A("key", nargs="?"), A("value", nargs="?")]),
    ("repos", cmd_repos, "Repos linked to the board and their local clones", []),
    ("review", cmd_review, "Digest of stories waiting on you: oldest wait first, smallest diff first (JSON; --html writes a page)", [
        A("--html", nargs="?", const=True, metavar="PATH", help="write a self-contained HTML page (default: review.html in the cgp home) and print its path")]),
    ("status", cmd_status, "Human-readable board overview: counts, what waits on you, workers, blocked stories", [
        A("--json", action="store_true", help="the raw snapshot instead of the table")]),
    ("list", cmd_list, "JSON snapshot of the board (also files closed issues under Done and clears answered questions)", [
        A("--brief", action="store_true")]),
    ("wait", cmd_wait, "Poll until a story becomes actionable, a worker is released, all is Done, or the timeout passes", [
        A("--timeout", type=int, default=540), A("--interval", type=int, default=0)]),
    ("approve", cmd_approve, "Approve the gate the story waits at (plan_review or pr_review); only you, in a terminal", [A("item")]),
    ("move", cmd_move, "Move a story to a column (human gates enforced)", [A("item"), A("column")]),
    ("set", cmd_set, "Set the plan or pr field of a story", [A("item"), A("field"), A("value")]),
    ("preview", cmd_preview, "Copy the deploy preview URL from the story's PR into the Preview field", [A("item")]),
    ("comment", cmd_comment, "Comment on the story (or its PR with --pr); body on stdin", [A("item"), A("--pr", action="store_true")]),
    ("ask", cmd_ask, "Ask the user a question (stdin) and park the story until they reply", [A("item")]),
    ("answers", cmd_answers, "Question and answer history of a story", [A("item")]),
    ("feedback", cmd_feedback, "New comments from trusted people since the agent last commented", [A("item")]),
    ("prepare", cmd_prepare, "Everything a worker reads before acting, in one call", [A("item")]),
    ("adopt", cmd_adopt, "Convert a draft item into an issue in a linked repo", [A("item"), A("repo")]),
    ("stop", cmd_stop, "Finish the current cycle, then stop the loop (a separate shell stops the live session holding the board's lock)", [
        A("board", nargs="?", help="board URL or key; default: this session"), A("--cancel", action="store_true")]),
    ("worker", cmd_worker, "Register (start/stop/clear) a worker, set its phase, or record its agent id (loop only)", [
        A("action", choices=["start", "stop", "clear", "phase", "agent"]), A("item", nargs="?"), A("column", nargs="?", help="agent: the id of the spawned Agent"),
        A("title", nargs="?", default="", help="the phase's detail text (worker phase); start looks the title up itself"),
        A("--outcome", choices=["fail", "ok", "waiting"], help="stop: how the run ended, for the 3-strikes rule (fail adds a strike; ok and waiting reset it; ok also records the worker for a send-back to resume)")]),
    ("resume", cmd_resume, "Loop only: get = may a send-back message the worker that last handled this story (read-only); clear = forget it", [
        A("action", choices=["get", "clear"]), A("item")]),
    ("worktree", cmd_worktree, "Create or reuse the story's git worktree", [A("item")]),
    ("worktree-remove", cmd_worktree_remove, "Remove the story's worktree and branch; refuses uncommitted or unpushed work", [
        A("item"), A("--discard", action="store_true", help="remove it even so (you, not an agent)")]),
    ("sync", cmd_sync, "Rebase the story's worktree onto its remote branch and the latest default branch", [A("item")]),
    ("stack", cmd_stack, "Show the stack a story is built on (stackedStories); --clear lifts it (you, in a terminal)", [
        A("item"), A("--clear", action="store_true")]),
    ("touches", cmd_touches, "Record (or read) the files and directories a story changes", [A("item"), A("paths", nargs="*")]),
    ("overlap", cmd_overlap, "Find stories touching the same files", [A("item")]),
    ("block", cmd_block, "Make a story wait for another (or --unblock)", [A("item"), A("other", nargs="?"), A("--unblock", action="store_true")]),
    ("guard", cmd_guard, "Fail when the branch changes guarded files (CI, CODEOWNERS, ...) the plan does not list", [A("item")]),
    ("ci-wait", cmd_ci_wait, "Wait for a PR's checks: green, red (with logs), none or pending", [
        A("repo"), A("pr", type=int), A("--timeout", type=int, default=540), A("--interval", type=int, default=20),
        A("--grace", type=int, default=120), A("--sha", help="first wait until the PR head is this commit (the one just pushed)")]),
    ("ci-triage", cmd_ci_triage, "After ci-wait says red: flaky (green after one rerun; files it once as a story on hold), main-broken or real", [
        A("repo"), A("pr", type=int), A("--timeout", type=int, default=540), A("--interval", type=int, default=20),
        A("--sha", help="refuse (verdict stale) unless the PR head is this commit")]),
    ("pr-state", cmd_pr_state, "State of a PR on a linked repo", [A("repo"), A("pr", type=int)]),
    ("merge", cmd_merge, "Squash-merge (auto-merge) the story's PR; only from PR Approved", [
        A("item"), A("--cancel", action="store_true", help="disable auto-merge on the story's PR instead of merging")]),
    ("merge-wait", cmd_merge_wait, "Wait for the story's PR to merge, keeping it up to date", [
        A("item"), A("--timeout", type=int, default=540), A("--interval", type=int, default=20)]),
    ("add", cmd_add, "Create an issue and put it on the board in Todo (body on stdin with --body -)", [
        A("title"), A("--repo"), A("--body"), A("--priority", help="High, Medium, Low or Hold")]),
    ("split", cmd_split, "Sub-stories: declare a split in the plan (--declare, JSON on stdin), or create the approved ones", [
        A("item"), A("--declare", action="store_true")]),
    ("defer", cmd_defer, "After the merge: file the PR's `## Deferred` entries as Todo stories on hold (safe to rerun)", [A("item")]),
    ("import", cmd_import, "Put open issues with a label on the board in Todo", [A("label"), A("--repo")]),
    ("models", cmd_models, "The model for each sub-agent role at a risk rating (null: the session's model)", [
        A("rating", choices=["low", "medium", "high"])]),
    ("rate", cmd_rate, "Record your risk rating of a story (the auto-approval policy reads it)", [A("item"), A("rating", choices=list(RATINGS))]),
    ("repo-config", cmd_repo_config, "The .cgp.json of a repo's default branch (test and lint commands, shared and guarded files, ...)", [
        A("target", help="owner/name or a story's item id")]),
    ("brief", cmd_brief, "A repo's cached one-page map for workers (status, text); --write stores one from stdin", [
        A("target", help="owner/name or a story's item id"), A("--write", action="store_true", help="store the text on stdin"),
        A("--sha", help="with --write: the default branch commit the text was written from")]),
    ("unstick", cmd_unstick, "Clear one story's operational state (worker, strikes, answered, Waiting On); you only, not agents", [
        A("item"), A("--dry-run", action="store_true", help="list what would be cleared; change nothing"),
        A("--kill", action="store_true", help="also stop its worker if it is still running")]),
    ("doctor", cmd_doctor, "Check the installation: gh, token scopes, board, clones, stale files", [
        A("--board", help="board URL; default: the only or bound board"),
        A("--deep", action="store_true", help="also read the board: worker rows of stories that are gone")]),
    ("gc", cmd_gc, "Delete state of dead sessions and worktrees of finished stories", [
        A("--days", type=int, default=7, help="age of session files to delete"), A("--dry-run", action="store_true")]),
    ("rules", cmd_rules, "House rules: propose rules from the review comments on merged cgp PRs (writes a file under the cgp home, never into the repo)", [
        A("action", choices=["propose"]), A("--repo"), A("--limit", type=int, default=50, help="merged cgp PRs to read, among the last 100 closed PRs"),
        A("--min", type=int, default=3, help="comments a point needs, across at least 2 PRs"), A("--out", help="write the proposal here instead"),
        A("--if-due", action="store_true", help="only when rulesProposeDays have passed since the last run")]),
]


def build_parser():
    ap = argparse.ArgumentParser(prog="cgp", description="GitHub Projects v2 helper for the cgp plugin. Output is JSON unless noted.")
    sp = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    for name, fn, help_, args in COMMANDS:
        p = sp.add_parser(name, help=help_, description=help_)
        for names, kw in args:
            p.add_argument(*names, **kw)
        p.set_defaults(fn=fn)
    return ap


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    a = build_parser().parse_args()
    a.fn(a)


__all__ = ["COMMANDS", "build_parser", "main", "die"]
