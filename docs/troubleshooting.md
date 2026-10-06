# Troubleshooting

Start with `scripts/cgp doctor` (or `/cgp:doctor`): it checks `gh`, the token scope, the board, each repo's clone and leftover files, and prints the fix for each problem.

| Symptom | Cause and fix |
|---|---|
| exit 3, "token lacks the 'project' scope" | `gh auth refresh -s project` (a browser step) |
| `cgp use` exit 5 | another live session runs this board's loop. Use `--takeover` only if that session is gone (a lock untouched for 30 minutes counts as abandoned) |
| exit 6 from `cgp use` or any command | the board cannot be told: several boards are set up and the current directory's repo is on none of them, the repo is listed on several boards (remove it from all but one), (a session bound with `cgp use` keeps its board wherever it runs). Run from the repo's checkout, or pass the board URL to `cgp use` |
| `merge` exit 7 / `merge-wait` state `changed` | the PR changed after you reviewed it: the worker sends it back to PR Review ([safety](safety.md)) |
| a story shows `Waiting On: You` but nothing was asked | it was set by hand; the loop clears it only when a question it asked got a reply. Clear the field yourself (`cgp status` lists these for Plan Approved and Implement stories) |
| a story in any column shows `Waiting On: Another story` but no story blocks it | it is queued behind the `concurrency` cap or (Plan Approved / Implement / skip-plan Todo) held back for a file overlap with a running story; `cgp status` shows the reason. The marker clears when it starts |
| a Todo or Plan story is not starting | with a `concurrency` of 2 or more the loop keeps one slot for it while no Todo or Plan worker runs, so later columns cannot starve it; with a cap of 1 it waits for the running worker, and `cgp status` shows `queued: all 1 worker slots are busy`. A story shown as waiting on you with nothing asked is flagged in `cgp status` in every column |
| "no local clone known for <repo>" | `scripts/cgp repo-path <owner/name> <path>` or `scripts/cgp discover` |
| a worker never finishes | the loop reports it as `stalled` after `maxWorkerMinutes` and releases the story |
| `~/.config/claude-github-project` is full of old files | `scripts/cgp gc --dry-run`, then `scripts/cgp gc` |
| Windows | not supported (the state lock uses `fcntl`); use WSL |
