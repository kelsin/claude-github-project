# Architecture

The plugin has three parts: skills (prompts), the `scripts/cgp` CLI that does every deterministic step, and a mod (the band above the prompt). See the [README](../README.md) for what they do; this page is how they fit together.

## Code layout

`scripts/cgp` is a three-line shim; the code is `scripts/cgp_lib/`:

| Module | Holds |
|---|---|
| `consts` | paths, column layout, phases, default settings |
| `util` | errors, JSON output, name normalising, `call` (run a command in-process) |
| `gh` | `gh` wrappers (GraphQL, paginated REST) and the comment trust model |
| `store` | everything under `~/.config/claude-github-project`: board configs, per-board data, session state, locks |
| `board` | the project: fields, items, columns, views, setup, board-level commands |
| `session` | session binding (`use`, `release`, `stop`) and the worker registry |
| `gitwt` | story worktrees: create, `sync` (rebase), `guard`, remove |
| `pr` | checks, CI waiting, merging, the review-bound merge gate |
| `story` | comments, questions, feedback, field updates, `move`, `prepare` |
| `sched` | which stories are actionable, file overlap and blocking, `list` / `status` / `wait` |
| `doctor` | `doctor` and `gc` |
| `intake` | `add` and `import` (stories onto the board) |
| `deferred` | `defer`: files the merged PR's `## Deferred` section as Todo stories on hold |
| `auto_intake` | the opt-in failing-main and dependency-PR intake run by `sched.snapshot` |
| `notify` | the `notifyCommand` hook |
| `review` | `review`: the digest of stories waiting on you (JSON or one HTML page) |
| `repoconf` | a repo's `.cgp.json` and `.cgp-rules.md` (read from its default branch) |
| `rules` | `rules propose`: house rules proposed from review comments on merged cgp PRs (the repo's `.cgp-rules.md` itself is read by `repoconf`) |
| `models` | the model per sub-agent role |
| `gitutil` | plain git helpers |
| `cli` | the command table (`COMMANDS`) and argument parsing |

Add a command by writing `cmd_<name>(a)` in the module it belongs to and adding one row to `COMMANDS` in `cli.py`; `scripts/gen-cli-docs` regenerates [cli.md](cli.md) from that table.

## Keeping up with main, and stories that collide

- Every time a worker is about to write code (implement, fix review findings, address PR feedback, resolve a merge problem) it runs `scripts/cgp sync <story>`, which fetches and rebases the story's worktree onto the latest default branch. Conflicts are resolved by the worker (a sub-agent for big ones), tested, and force-pushed with lease; if it can't resolve one with confidence it asks you.
- Each plan records the files and directories it changes (`cgp touches`). Before implementing, the worker runs `cgp overlap`, which compares them with every other in-flight story (plan files, plus the real file list of any open PR). When another story shares a file and is further along (or at the same stage with a lower issue number), this one is ordered behind it with `cgp block`: it stays in Plan Approved with a status comment saying who it waits for, the loop skips it, and it is released the moment the blocker is Done and then rebased onto the new main. Directory-level overlaps never block, and neither do overlaps on shared files (`sharedFiles` setting: lockfiles, schemas, locales, snapshots and Markdown by default); they are noted in the plan or PR. The ordering is a strict stage-then-number order, and `block` refuses to create cycles.
- GitHub's own issue dependencies ("blocked by") are read too (`nativeDependencies` setting, on by default): a story whose blocker is an open issue on the same board waits for it whatever the stages or numbers, exactly like a `cgp block`, and `list` and `status` show the same set. Blockers that are not on the board are ignored, and a story with more than 10 dependencies waits (cgp reads 10, so it cannot tell). `cgp block` stays local: overlap blocks are never written to GitHub, because a native edge does not go away when the ranks flip. The edges cgp writes natively are the ones a plan declares as an order between its own stories (never on draft items); if GitHub refuses one (older GitHub Enterprise Server, no permission), the order is kept in cgp's own data instead. A cgp overlap block that would close a cycle with a GitHub dependency is dropped; a cycle made only of GitHub dependencies cannot be broken by cgp, so it goes to you as a question. `cgp doctor` checks the field exists, and a server without it falls back to cgp's blocks alone.
- The mod shows how many stories are queued this way.
- Every poll removes the worktree and `cgp/<n>` branch of each Done or closed story that has no worker, unless that would lose work: uncommitted changes, commits that are neither on the default branch nor on `origin/cgp/<n>`, a merged PR whose head is not the worktree's HEAD, an unknown default branch or a failed fetch all keep it. A kept worktree is looked at again an hour later (`cgp gc` applies the same checks).

## Sub-agent sizing

Workers rate each story low / medium / high on complexity and risk (auth, migrations, money, public APIs, infra, concurrency, irreversibility) and size planners, reviewers and implementers from it, e.g. 1 / 2 / 3-4 reviewers with different lenses. The rating is posted in the status comment. Sub-agents are mandatory: every planning, review, implementation and fix phase spawns at least one (the status comment lists the counts per phase). A worker works inline only after the Agent tool is missing or a spawn call fails, and says so in a status comment quoting the error.

## Parallel sessions (one board per session)

Run `/cgp:run` in as many sessions as you like, as long as each uses a different board (`/cgp:setup` each board once). A repo belongs to one board, so the board comes from the repo you run in (the `origin` remote of the current directory; story worktrees count too). `/cgp:run <board-url>` picks one explicitly and wins over the repo; outside any board's repo the session's bound board, or the only board, is used, else exit 6.
- **Per-session UI and state.** The mod gives each session an id (`CGP_SESSION`, exported to everything that session runs), so each band shows only that session's board, counts and workers, and one session's workers are never cleared by another. A session that never ran `/cgp:run` shows no band.
- **One loop per board.** `cgp use` claims the board for the session (a lock file kept alive by every update the session makes; a lock untouched for 30 minutes counts as abandoned). A second session asking for the same board is refused, and offered `--takeover` if the first is gone; the session that was taken over stops at its next poll. `cgp release` frees the board (the loop does this when everything is Done).
- **Board data outlives sessions.** Blocks, epic ordering, touched-file lists and feedback cursors are stored per board, so restarting a session loses nothing. Repo clone paths are shared by all boards.
- If a session was started before the mod was loaded, `cgp` falls back to a `default` session id (`default-<board>` inside a board's repo, so terminals on different boards stay apart; it changes if you `cd` out of the repo), which is fine for a single session. Each worker row shows the emoji of the story's current column; it updates as the worker moves the story.

## Files

- `~/.config/claude-github-project/boards/<project id>.json`: one board's ids, fields, linked repos, settings; `<project id>.data.json`: its blocks, touches and feedback cursors.
- `~/.config/claude-github-project/paths.json`: repo to local-clone map, shared by all boards.
- `~/.config/claude-github-project/state-<session id>.json`: one session's live state (counts, waiting stories, workers) that its mod reads; `locks/<project id>.json`: which session runs a board.
- `~/.config/claude-github-project/worktrees/`: one git worktree per story (`cgp/<issue number>` branches, under `<owner>/<repo>/<number>`).
- `~/.config/claude-github-project/plans/`: plan HTML sources (published as Claude artifacts).
