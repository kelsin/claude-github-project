# claude-github-project (`cgp`)

A Claude Code plugin that turns a GitHub Projects v2 board into an agent pipeline. Stories move through planning, plan review, implementation, PR review and merge; you only approve plans and PRs.

- **Skills**: `/cgp:setup` configures a board, `/cgp:run` runs the loop, `/cgp:add` adds a story (or imports issues by label), `/cgp:status` shows the board, `/cgp:stop` stops the loop cleanly, `/cgp:doctor` checks the installation.
- **`scripts/cgp`**: a Python CLI doing every deterministic step (GraphQL, comments, CI polling, merging, worktrees), so agents only make decisions. Needs Python 3.8+, `git` and `gh`; runs on macOS, Linux and native Windows (with [Git for Windows](https://gitforwindows.org)).
- **A mod**: a band above the prompt showing the board link, what is waiting on you, and one line per active worker ([details](docs/mod.md)).

## Install

```bash
claude plugin marketplace add kelsin/claude-github-project
claude plugin install cgp@claude-github-project
```

Then restart Claude Code (or reload plugins). Your `gh` token needs the `project` scope; if it is missing, setup tells you to run `gh auth refresh -s project`.

## Use

1. From a checkout of one of your repos: `/cgp:setup https://github.com/orgs/<org>/projects/<n>` (or `/users/<user>/projects/<n>`). [Details](docs/setup.md).
2. `/cgp:run`, then add stories to Todo on the board and leave it running. It ends when every story is Done or you stop it.

## How it works

| Column | Who acts |
|---|---|
| 🆕 Todo | agents pick the repo |
| 🧠 Plan | agents write and review a plan |
| 🙋 Plan Review | **you** approve or comment |
| ✅ Plan Approved | agents |
| 🔨 Implement | agents implement and review a PR |
| 🚦 PR Review | **you** approve or comment |
| 🚀 PR Approved | agents merge |
| 🎉 Done | |

Agents ask questions as comments on the story and wait for your reply. Per-story fields (`Priority`, `Plan: Skip`, `Auto Approve`) adjust the flow. See [Workflow](docs/workflow.md).

## Docs

- [Setup](docs/setup.md): what `/cgp:setup` does, running and stopping
- [Workflow](docs/workflow.md): columns, dispatch, story fields, questions
- [Safety](docs/safety.md): threat model, human gates, recommended permission rules
- [Settings](docs/settings.md) and the [CLI reference](docs/cli.md)
- [The mod](docs/mod.md), [troubleshooting](docs/troubleshooting.md), [the ten-column layout](docs/migration.md)
- [Architecture](docs/architecture.md) and [Development](docs/development.md) (tests, releases)
- [Changelog](CHANGELOG.md)

## License

MIT, see [LICENSE](LICENSE).
