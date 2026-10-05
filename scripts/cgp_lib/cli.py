"""Command table and argument parsing: every `cgp <command>` is one row of COMMANDS."""
import argparse


from .board import (cmd_adopt, cmd_config, cmd_discover, cmd_repo_path, cmd_repos, cmd_setup)
from .doctor import cmd_doctor, cmd_gc
from .gitwt import cmd_guard, cmd_sync, cmd_worktree, cmd_worktree_remove
from .pr import cmd_ci_wait, cmd_merge, cmd_merge_wait, cmd_pr_state
from .sched import cmd_block, cmd_list, cmd_overlap, cmd_status, cmd_touches, cmd_wait
from .session import cmd_release, cmd_session_title, cmd_stop, cmd_use, cmd_worker
from .story import (cmd_answers, cmd_ask, cmd_comment, cmd_feedback, cmd_move, cmd_prepare, cmd_preview, cmd_set)
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
    ("config", cmd_config, "Show or set a board setting (concurrency, pollSeconds, autoMigrate, remoteControl, sharedFiles, ...)", [
        A("key", nargs="?"), A("value", nargs="?")]),
    ("repos", cmd_repos, "Repos linked to the board and their local clones", []),
    ("status", cmd_status, "Human-readable board overview: counts, what waits on you, workers, blocked stories", [
        A("--json", action="store_true", help="the raw snapshot instead of the table")]),
    ("list", cmd_list, "JSON snapshot of the board (also files closed issues under Done and clears answered questions)", [
        A("--brief", action="store_true")]),
    ("wait", cmd_wait, "Poll until a story becomes actionable, a worker is released, all is Done, or the timeout passes", [
        A("--timeout", type=int, default=540), A("--interval", type=int, default=0)]),
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
    ("worker", cmd_worker, "Register (start/stop/clear) a worker or set its phase", [
        A("action", choices=["start", "stop", "clear", "phase"]), A("item", nargs="?"), A("column", nargs="?"),
        A("title", nargs="?", default="", help="the phase's detail text (worker phase); start looks the title up itself")]),
    ("worktree", cmd_worktree, "Create or reuse the story's git worktree", [A("item")]),
    ("worktree-remove", cmd_worktree_remove, "Remove the story's worktree and branch", [A("item")]),
    ("sync", cmd_sync, "Rebase the story's worktree onto its remote branch and the latest default branch", [A("item")]),
    ("touches", cmd_touches, "Record (or read) the files and directories a story changes", [A("item"), A("paths", nargs="*")]),
    ("overlap", cmd_overlap, "Find stories touching the same files", [A("item")]),
    ("block", cmd_block, "Make a story wait for another (or --unblock)", [A("item"), A("other", nargs="?"), A("--unblock", action="store_true")]),
    ("guard", cmd_guard, "Fail when the branch changes guarded files (CI, CODEOWNERS, ...) the plan does not list", [A("item")]),
    ("ci-wait", cmd_ci_wait, "Wait for a PR's checks: green, red (with logs), none or pending", [
        A("repo"), A("pr", type=int), A("--timeout", type=int, default=540), A("--interval", type=int, default=20),
        A("--grace", type=int, default=120), A("--sha", help="first wait until the PR head is this commit (the one just pushed)")]),
    ("pr-state", cmd_pr_state, "State of a PR on a linked repo", [A("repo"), A("pr", type=int)]),
    ("merge", cmd_merge, "Squash-merge (auto-merge) the story's PR; only from PR Approved", [
        A("item"), A("--cancel", action="store_true", help="disable auto-merge on the story's PR instead of merging")]),
    ("merge-wait", cmd_merge_wait, "Wait for the story's PR to merge, keeping it up to date", [
        A("item"), A("--timeout", type=int, default=540), A("--interval", type=int, default=20)]),
    ("doctor", cmd_doctor, "Check the installation: gh, token scopes, board, clones, stale files", [
        A("--board", help="board URL; default: the only or bound board")]),
    ("gc", cmd_gc, "Delete state of dead sessions and worktrees of finished stories", [
        A("--days", type=int, default=7, help="age of session files to delete"), A("--dry-run", action="store_true")]),
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
    a = build_parser().parse_args()
    a.fn(a)


__all__ = ["COMMANDS", "build_parser", "main", "die"]
