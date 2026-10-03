---
name: run
description: Use to start the agentic loop that works every story on the configured GitHub Project board through plan, plan review, implementation, PR review and merge. Runs until all stories are Done or the user stops it.
---

# cgp run

Runs the board loop. It never stops on its own except when every story is Done; the user stops it. Be terse: no narration between cycles beyond the one-line updates below.

The CLI is `scripts/cgp` at the plugin root, two directories above this skill's base directory. Resolve it to an absolute path once (`CGP`) and pass that absolute path to every worker. The column prompts are in `columns/` next to this file.

## Start

1. `CGP list --brief`. If it errors with "no board configured", tell the user to run `/cgp:setup <board-url>` and stop.
2. `CGP worker clear` (drops stale workers from an earlier crashed run).
3. Tell the user the board URL and the counts once.

## Cycle (repeat forever)

1. `CGP list --brief` → JSON with `status`, `batch`, `counts`, `waitingOnYou`.
2. `status: "done"`: report "all stories are Done" and stop. This is the only way the loop ends by itself.
3. `status: "work"`:
   - `batch` holds every actionable story (no cap by default). If the user set a cap (`settings.concurrency` > 0) and `actionableTotal` is larger, the rest wait for a later cycle.
   - For each story in `batch`, register it so the UI shows it: `CGP worker start <item> <column> "<title>"` (one Bash call for the whole batch).
   - Spawn ONE Agent per story, all in a single message, each with `run_in_background: false` so the call returns when every worker is done. `subagent_type: "general-purpose"`. Prompt (fill in the placeholders; do not inline the column file):

     ```
     You are the worker for one board story. CGP=<absolute path to scripts/cgp>.
     Read <skills/run dir>/columns/shared.md, then <skills/run dir>/columns/<column>.md, and follow them exactly.
     Story: <the story's JSON from the batch>
     When finished (or blocked) run `CGP worker stop <item>` and reply with one line: "<title>: <outcome>".
     ```
   - After all workers return: `CGP worker clear`, then go straight to the next cycle. A story's new column is handled by the next cycle, not by the same worker.
   - Print one line per story: `<emoji> <title>: <outcome>`.
4. `status: "idle"` (nothing an agent can do: every remaining story is in Plan Approval or PR Approval or waiting on the user's answer):
   - If the set of waiting stories changed since the last idle report, print one short list: stories in `waitingOnYou` (title and url only), stories in `blocked` (title and who they wait for), and the Plan Approval / PR Approval counts.
   - `CGP wait --timeout 540` via Bash (set the Bash timeout to 560000). It polls the board every `pollSeconds` and returns as soon as a story becomes actionable (the user answered, approved, or moved something), all stories are Done, or the timeout passes. Then start the next cycle. Do not sleep any other way.

## Rules

- The user decides the Plan Approval and PR Approval columns. Never move a story out of them or work on them.
- Stories in Plan Approved that wait on another story (`blocked` in the list output) are skipped until their blockers are Done; they do not count toward the no-progress rule.
- A worker failure (agent error, crash) must not end the loop: note it in one line, leave the story where it is, and continue.
- Track `(item, column)` after each cycle. If a story is still in the same column after 3 consecutive cycles of being worked (crashes or no progress), run `CGP ask <item>` with a short summary so it waits on the user instead of spinning. Keep the per-cycle output short; the loop may run for days.
- Closed issues are filed under Done by the CLI; ignore them. An empty board reports `done`: say "no stories on the board".
- Do not do story work yourself; you only dispatch, wait and report.
