# Dispatcher daemon (experimental)

`cgp daemon` runs the `/cgp:run` loop as a plain process instead of inside a chat session: it snapshots the board, starts one headless `claude -p` worker per actionable story, waits, and repeats. The chat loop stays the default; the daemon only exists to run without a session. Humans still approve every plan and PR.

```bash
scripts/cgp doctor                 # checks that `claude` is on PATH and knows the flags the daemon passes
scripts/cgp daemon --dry-run       # print what would be dispatched, start nothing
scripts/cgp daemon --once          # one cycle: dispatch, wait for those workers, exit
scripts/cgp daemon --verbose       # also log every cycle
scripts/cgp daemon                 # run until every story is Done, or stop it
```

It claims the board's lock like `cgp use` does (exit 5 when another session or daemon holds it; a chat loop and a daemon never run the same board). With several boards and a working directory that is on none of them, name the board: `CGP_BOARD=<board key or URL> scripts/cgp daemon`. `CGP_BOARD` is a general board override: every `cgp` command reads it before anything else, so it also works for one-off commands in a shell. The first SIGINT/SIGTERM (or `cgp stop <board url>`) lets running workers finish and dispatches nothing new; a second signal kills them.

## What a worker gets

- A prompt on **stdin** with the absolute path of `scripts/cgp`, the columns directory, the item id, column, issue number and repo. Ids are checked against a strict pattern first; a story whose ids do not match is skipped. Titles never reach the command line, stdin or the environment; the worker reads them with `cgp prepare`.
- A neutral working directory (`~/.config/claude-github-project/daemon`, no repo and so no project settings), its own process group, and only these variables: `PATH`, `HOME`, `USER`, `LOGNAME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TERM`, `SHELL`, `TMPDIR`, `TZ`, `XDG_CONFIG_HOME`, `CLAUDE_CONFIG_DIR`, the Anthropic credentials variables (`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_OAUTH_TOKEN`), plus `CGP_HOME`, `CGP_SESSION`, `CGP_BOARD` and `CGP_DAEMON=1`. No `GH_TOKEN`: `gh` must be logged in through its own credential store.
- Fixed flags: `-p --output-format stream-json --verbose --max-turns <daemonMaxTurns> --max-budget-usd <...> --permission-mode dontAsk --setting-sources user --strict-mcp-config --allowedTools ... --disallowedTools ...`. With `dontAsk`, anything not allowed is denied, nobody is asked.
- Allowed: `Read`, `Glob`, `Grep`, `Agent`, `Task`, `TodoWrite`, `Bash` for `scripts/cgp`, these `git` subcommands (`status`, `diff`, `log`, `show`, `add`, `commit`, `fetch`, `rebase`, `merge-base`, `rev-parse`, `ls-files`, `checkout`, `switch`, `worktree`, `branch`, `push`) and these `gh` verbs (`pr view`, `pr diff`, `pr list`, `pr create`, `pr reopen`), `Edit`/`Write` inside the story's own worktree and the plans directory, and whatever `daemonAllowedTools` adds. Rules are comma-joined on the command line, so a rule cannot contain a comma.
- Denied, whatever the settings say: `gh api`, `gh pr merge`, `gh auth`, `gh gist`, `gh secret`, `gh workflow`, `gh release`, `gh repo`, `gh issue close`, `git -c`, `git config`, `git credential`, `git remote`, `git ls-remote`, `git --exec-path`, `curl`, `wget`, `cgp config`, `Edit`/`Write` on cgp's boards, locks, logs, state and `paths.json`, and on `~/.claude`, and `Read`/`Glob`/`Grep` on `~/.config/gh`, `~/.ssh`, `~/.aws`, `~/.claude`, any `.env*` file and cgp's `boards/`, `logs/` and `paths.json`. When the plugin itself is installed under `~/.claude`, only that directory's credentials, settings, `projects/`, history and `CLAUDE.md` are denied, so the worker can still read its prompts.
- Plan links set with `cgp set <item> plan` are accepted only on claude.ai or as a comment on the story's own issue. Planning needs the `Artifact`, `ArtifactComments` and `ToolSearch` tools, which are not in the default allowlist: unless you add them to `daemonAllowedTools`, a headless worker cannot publish a plan artifact and falls back to a plan comment on the story's own issue. A worker asks with `cgp ask` when any other step cannot be done.

The denylist is defence in depth, not a sandbox (see [safety](safety.md)): the scope of your `gh` token and branch protection are the real limits.

## Failures, deadlines and spend

- Each cycle's finished runs are judged. A crash, an error result, a timeout, or a run that leaves the story in the same column is a strike for that story and column (`daemonStrikes` in the board data); three strikes park the story with `cgp ask` (a draft cannot be asked about, so after three strikes the daemon stops dispatching it and logs that). A reply starting `waiting:` or `blocked:` is not a strike only when the board really shows a question to you or a story it waits on. Progress clears the strikes.
- A worker running longer than `maxWorkerMinutes` is killed together with its process group and counts as a strike.
- The worker registry entry carries the process id and a dispatch token, and the daemon clears only an entry holding its own token, so a re-dispatched story is never un-registered by the previous run.
- Spend is read from each run's result. `daemonMaxBudgetUsd` caps one run (and is cut down to what the story and daemon caps have left), `daemonStoryBudgetUsd` caps a story across runs (past it the story is parked with a question), `daemonTotalBudgetUsd` caps one daemon run (past it nothing new is dispatched and the daemon ends when its workers do). The per-story figure is kept in the board data (`daemonSpend`). See [settings](settings.md); all `daemon*` settings can only be changed by you at a terminal.

## Logs

One file per run under `~/.config/claude-github-project/logs/<owner>-<repo>-<number>/<timestamp>-<column>.log` (directory 0700, files 0600): the worker's stream-json output. They can contain whatever the worker read, so treat them like the worktrees. They are never pruned: delete old ones yourself.

## Not included

Surviving a reboot (launchd, systemd) and putting a `gh` shim on the workers' PATH are follow-ups: run the daemon under your own supervisor for now.
