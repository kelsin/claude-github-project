# claude-github-project (`cgp`)

A Claude Code plugin that turns a GitHub Projects v2 board into an agent pipeline. Stories move through planning, plan review, implementation, PR review and merge; you only approve plans and PRs.

It has three parts:

- **Skills**: `/cgp:setup` configures a board, `/cgp:run` runs the loop.
- **`scripts/cgp`**: a Python CLI doing every deterministic step (GraphQL, comments, CI polling, merging, worktrees), so agents only make decisions. Python 3.8+ and `gh` are the only requirements.
- **A mod**: a band above the prompt showing the board link, what is waiting on you, and one line per active worker.

## Install

```bash
claude plugin marketplace add ~/src/claude-github-project
claude plugin install cgp@claude-github-project
```

Then restart Claude Code (or reload plugins). Your `gh` token needs the `project` scope; if it is missing, setup tells you to run:

```bash
gh auth refresh -s project
```

## Use

1. From a checkout of one of your repos: `/cgp:setup https://github.com/orgs/<org>/projects/<n>` (or `/users/<user>/projects/<n>`).
   - Replaces the Status options with the 8 columns below (a board in the old ten-column layout is migrated, see below), adds fields `Waiting On`, `Plan`, `PR`, `Preview` (the Netlify deploy preview URL, copied from the PR by `cgp preview`) and `Auto Approve` (see below; run setup again on an existing board to get it), links the current repo to the board and records its local path. It also adds three tabs if the board lacks them (an untouched starter "View 1" becomes the first): **Tasks** (table), **Board** (columns by Status) and **Approvals** (Status board filtered to Plan Review and PR Review). GitHub's API can't set a view's grouping or sort, so turn on group-by Repository in Board and Approvals yourself if you want it; existing views are never changed.
   - Existing items whose old status name matches a new column keep it, closed issues go to Done, everything else lands in Todo. Closed issues anywhere on the board are filed under Done.
   - Run it again from another repo's checkout (same URL) to link more repos. Repos without a known local path are asked for.
2. `/cgp:run`. Add stories to Todo on the board (issues, or draft items which get converted to issues in the repo the agent picks) and leave it running. It ends only when every story is Done, or when you stop it. To stop cleanly, press **Stop after this cycle** in the board band above the prompt (or run `scripts/cgp stop [board-url-or-key]` from a separate shell; the argument may be left out when exactly one live session holds a board lock, otherwise pass the board URL): the loop lets the running workers finish, then stops before dispatching more. `/cgp:run` picks up from the board.

## Columns

| Column | Who acts | What happens |
|---|---|---|
| 🆕 Todo | agents | Pick repo, move to Plan |
| 🧠 Plan | agents | Planners write an HTML plan artifact linked on the story, reviewers review it, updaters apply findings, move to Plan Review. Also where a story lands when you send it back (or it stalled): comments on the plan and the story are resolved, the changes re-reviewed |
| 🙋 Plan Review | **you** | Read the plan artifact. Comment and move to Plan, or move to Plan Approved |
| ✅ Plan Approved | agents | Move to Implement |
| 🔨 Implement | agents | Implement in a worktree, open PR, reviewers review it while CI runs, fixers fix, CI green, move to PR Review. Also where a story lands when you send it back: requested changes on the PR are fixed and re-reviewed |
| 🚦 PR Review | **you** | Review the PR. Comment and move to Implement, or move to PR Approved |
| 🚀 PR Approved | agents | Squash merge (auto-merge once CI is green), fix conflicts and CI until merged, move to Done |
| 🎉 Done | nobody | |

Colors: Todo blue, Plan yellow, Plan Review purple, Plan Approved blue, Implement red, PR Review purple, PR Approved blue, Done green.

**Skip planning.** Type `Skip` in a Todo story's `Plan` field and no plan is made: the worker writes `- Plan: Skip` into the story description instead of a plan link and moves the story from Todo straight to Implement, working from the description and iterating there.

**Auto approve.** Set a story's `Auto Approve` field to `Plan`, `PR` or `Both` and the agent passes that gate itself: a planned story goes to Plan Approved (instead of Plan Review) and a story with a PR goes to PR Approved (instead of PR Review), each noted in its status comment. `cgp move` allows it only from Plan / Implement and only for the gate the field names; only you can set the field. With `PR` the code is never seen by you before merge, so the "changed after approval, send back to PR Review" rule doesn't apply to that story. `Skip` plus `Both` makes a story fully hands-off.

Planning and review share one worker run (as do implementing and PR review), so a story is picked up by a fresh worker only after Todo, Plan Approved and PR Approved. The mod shows which phase each worker is in (see below).

**Upgrading a board from the ten-column layout** (it had agent-side Plan Review and PR Review columns, and the human ones were called Plan Approval and PR Approval): nothing to do. `/cgp:run` brings the board up to date when it starts, under the board's lock: stories in the old agent-side columns move to Plan / Implement (where the worker now reviews them), Plan Approval / PR Approval are renamed Plan Review / PR Review with their stories where they were, and the old columns are removed. It reports what it changed. `scripts/cgp migrate --dry-run` shows the effect first, `scripts/cgp migrate` does it by hand, and `scripts/cgp config autoMigrate 0` stops `/cgp:run` from doing it (the board then keeps working in its old layout). A failed migration never blocks the run and is retried next time.

The loop dispatches one background worker per actionable story (no cap by default) and keeps watching the board: a story that arrives while others are mid-implementation is picked up on the next poll, and a finished worker's story is dispatched again in its new column. When several stories are ready to start and some touch the same files (per `cgp touches`), the loop starts the largest group that doesn't collide and holds the rest back until their rivals finish, instead of spending workers on stories that would only block. `concurrency` caps workers in flight. When only stories in your columns, or waiting on your answers, are left, it polls the board every 30 seconds and resumes the moment something changes.

## Keeping up with main, and stories that collide

- Every time a worker is about to write code (implement, fix review findings, address PR feedback, resolve a merge problem) it runs `scripts/cgp sync <story>`, which fetches and rebases the story's worktree onto the latest default branch. Conflicts are resolved by the worker (a sub-agent for big ones), tested, and force-pushed with lease; if it can't resolve one with confidence it asks you.
- Each plan records the files and directories it changes (`cgp touches`). Before implementing, the worker runs `cgp overlap`, which compares them with every other in-flight story (plan files, plus the real file list of any open PR). When another story shares a file and is further along (or at the same stage with a lower issue number), this one is ordered behind it with `cgp block`: it stays in Plan Approved with a status comment saying who it waits for, the loop skips it, and it is released the moment the blocker is Done and then rebased onto the new main. Directory-level overlaps never block, and neither do overlaps on shared files (`sharedFiles` setting: lockfiles, schemas, locales, snapshots and Markdown by default); they are noted in the plan or PR. The ordering is a strict stage-then-number order, and `block` refuses to create cycles.
- The mod shows how many stories are queued this way.

## Safety

The loop runs unattended with your `gh` token, and it reads text anyone can write on a linked repo. The design assumes that text is hostile:

- **Only trusted people can steer it.** `feedback`, `answers` and question replies only use comments from you, repo owners, and collaborators with write access (checked through the API). Everything else is reported as `ignoredUntrusted` with no body. The agent marker only counts on comments posted by your own gh account, so it can't be forged.
- **The human gates are enforced in code, not just in prompts.** (A story whose `Auto Approve` field you set is the one exception, see above.) `cgp move` refuses to move a story into Plan Approved or PR Approved, out of Plan Review or PR Review, or to Done unless its PR is merged. `cgp merge` works only on a story that is in PR Approved and only for that story's own PR. The `PR` field must name a PR on a repo linked to the board.
- **Why a custom `PR` field instead of GitHub's built-in "Linked pull requests"?** The built-in field is read-only through the API and anyone with write access to the issue can change it (keyword links and sidebar links), and it can hold several PRs, including cross-repo ones. `cgp merge` and Done must act on exactly one PR the loop itself opened, so only `cgp set` writes `PR`, and it validates the URL against the board's linked repos. Keyword auto-linking also only happens for PRs targeting the default branch and can lag PR creation.
- **Prompts treat everything written by people as data** (no running commands, fetching URLs, adding dependencies or leaking tokens because text said so), and `cgp guard` fails any branch that touches `.github/` or CODEOWNERS without the approved plan listing it.
- **Code changed after your PR approval is re-approved**: a fix that is more than a clean, conflict-free rebase (conflict resolution, CI-fix commits) sends the story back to PR Review instead of merging, and auto-merge is disarmed before any push.
- `~/.config/claude-github-project` is private to your user (0700/0600).

What it can't do: an agent with Bash can still call `gh` directly. For defence in depth consider Claude Code permission rules denying `Bash(gh pr merge:*)`, `Bash(gh auth token:*)` and `Bash(gh api graphql:*)` for sessions running the loop, and a fine-grained token (or `GH_TOKEN`) limited to the linked repos and without the `workflow` scope. The setup scripts need `project`; the loop does not need `workflow`.

## Questions

Agents ask questions as a comment on the story, with numbered questions each carrying a proposed default (reply "defaults ok" or answer some). The story gets `Waiting On: You` on the board (a story queued behind another story shows `Waiting On: Another story` instead, set and cleared by `cgp list`) and is skipped until you reply on the issue. Any new non-bot comment without the agent marker counts as a reply; the field clears itself and the worker resumes with the whole Q&A history. When a 4th round of questions is asked on one story, the comment adds a note suggesting it be rescoped or split. Agents prefer writing assumptions into the plan (an explicit "Assumptions" section) over asking, so Plan Approval is where you review them.

## Sub-agent sizing

Workers rate each story low / medium / high on complexity and risk (auth, migrations, money, public APIs, infra, concurrency, irreversibility) and size planners, reviewers and implementers from it, e.g. 1 / 2 / 3-4 reviewers with different lenses. The rating is posted in the status comment. Whether a worker can spawn its own sub-agents depends on your Claude Code build; if it cannot, it does the same passes itself in sequence.

## The mod

Installing the plugin adds a band above the prompt while a loop is running (it hides itself when no worker is active and the session's state has not been refreshed for 15 minutes). The mod is loaded in every session, but each session only shows its own state:

```
📋 My Board  🙋 Plan Review: 2  🚦 PR Review: 1  ❓ Waiting on you: 1
❓ Rename the export flag waiting on you
🔨 Add CSV export · implementing
🔍 Fix login redirect · reviewing (3 reviewers)
⏳ Bump the retry limit · waiting on CI
```

Each worker row shows what it is doing, set by the worker with `cgp worker phase <item> <planning|reviewing|revising|implementing|fixing|ci|merging> [detail]`; the CLI also sets phases by itself, so a worker that forgets still shows something true: planning or implementing from its column, reviewing once the plan is published or the PR is opened, ci while `ci-wait` runs (then the previous phase returns), merging during `merge`. Workers call `worker phase` for what the CLI cannot see (revising, fixing) and for the detail. A move resets the phase.

The board name and each waiting story are links (the story link opens the issue with the question). A toast appears when a story newly starts waiting on you; stories already waiting when the session starts are shown but not toasted.

## Parallel sessions (one board per session)

Run `/cgp:run` in as many sessions as you like, as long as each uses a different board (`/cgp:setup` each board once; `/cgp:run <board-url>` picks one, or it asks when several are set up).
- **Per-session UI and state.** The mod gives each session an id (`CGP_SESSION`, exported to everything that session runs), so each band shows only that session's board, counts and workers, and one session's workers are never cleared by another. A session that never ran `/cgp:run` shows no band.
- **One loop per board.** `cgp use` claims the board for the session (a lock file kept alive by every update the session makes; a lock untouched for 30 minutes counts as abandoned). A second session asking for the same board is refused, and offered `--takeover` if the first is gone; the session that was taken over stops at its next poll. `cgp release` frees the board (the loop does this when everything is Done).
- **Board data outlives sessions.** Blocks, touched-file lists and feedback cursors are stored per board, so restarting a session loses nothing. Repo clone paths are shared by all boards.
- If a session was started before the mod was loaded, `cgp` falls back to a `default` session id, which is fine for a single session. Each worker row shows the emoji of the story's current column; it updates as the worker moves the story.

## Files

- `~/.config/claude-github-project/boards/<project id>.json`: one board's ids, fields, linked repos, settings; `<project id>.data.json`: its blocks, touches and feedback cursors.
- `~/.config/claude-github-project/paths.json`: repo to local-clone map, shared by all boards.
- `~/.config/claude-github-project/state-<session id>.json`: one session's live state (counts, waiting stories, workers) that its mod reads; `locks/<project id>.json`: which session runs a board.
- `~/.config/claude-github-project/worktrees/`: one git worktree per story (`cgp/<issue number>` branches, under `<owner>/<repo>/<number>`).
- `~/.config/claude-github-project/plans/`: plan HTML sources (published as Claude artifacts).

Settings: `scripts/cgp config remoteControl 0` (`/cgp:run` turns on Remote Control for its session when it runs in the Claude desktop app, so you can follow and steer the loop from claude.ai or the phone; 0 skips that), `scripts/cgp config concurrency 3` (cap on parallel workers; default 0 = no cap), `scripts/cgp config pollSeconds 60`, `scripts/cgp config sharedFiles 'package-lock.json,*.schema.json,src/defaults.json'` (comma-separated fnmatch globs for files many stories edit, whose overlaps are reported under `shared` instead of blocking; empty disables).

## CLI

`scripts/cgp --help` lists everything. Notable: `use`/`release` (bind and claim a board), `migrate`, `prepare` (everything a worker reads before acting, in one call), `list` (board snapshot, also clears answered questions), `wait`, `move`, `set`, `ask`, `feedback`, `ci-wait`, `merge`, `merge-wait`, `worktree`.

## Develop

```bash
python3 -m unittest discover -s tests          # CLI against a fake gh and real temp git repos
claude plugin test .                           # mod tests (hooks/*.test.ts); needs a Claude Code build that knows mods
                                               # (validating the plugin itself: point `claude plugin validate` at a copy without marketplace.json;
                                               # builds older than ~2.1.280 warn about `types`/`modules` and skip the band)
```

Not covered by tests: the real GitHub GraphQL schema and a live agent run. Test against a throwaway board first.
