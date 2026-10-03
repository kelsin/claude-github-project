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
   - Replaces the Status options with the 10 columns below, adds fields `Waiting On`, `Plan`, `PR`, `Repo`, links the current repo to the board and records its local path.
   - Existing items whose old status name matches a new column keep it; everything else lands in Todo.
   - Run it again from another repo's checkout (same URL) to link more repos. Repos without a known local path are asked for.
2. `/cgp:run`. Add stories to Todo on the board (issues, or draft items which get converted to issues in the repo the agent picks) and leave it running. It ends only when every story is Done, or when you stop it.

## Columns

| Column | Who acts | What happens |
|---|---|---|
| 🆕 Todo | agents | Pick repo, move to Plan, planners write an HTML plan artifact linked on the story, move to Plan Review |
| 🧠 Plan | agents | Story sent back (or stalled): resolve comments on the plan and the story, move to Plan Review |
| 🔍 Plan Review | agents | Reviewers review the plan, updaters apply findings, artifact refreshed, move to Plan Approval |
| 🙋 Plan Approval | **you** | Read the plan artifact. Comment and move to Plan, or move to Plan Approved |
| ✅ Plan Approved | agents | Move to Implement, implement in a worktree, open PR, fix CI, move to PR Review |
| 🔨 Implement | agents | Story sent back: fix issues and requested changes on the PR, move to PR Review |
| 👀 PR Review | agents | Reviewers review the PR, fixers fix immediately, CI green, move to PR Approval |
| 🚦 PR Approval | **you** | Review the PR. Comment and move to Implement, or move to PR Approved |
| 🚀 PR Approved | agents | Squash merge (auto-merge when possible), fix conflicts and CI until merged, move to Done |
| 🎉 Done | nobody | |

Colors: Todo blue, Plan yellow, Plan Review orange, Plan Approval purple, Plan Approved blue, Implement red, PR Review pink, PR Approval purple, PR Approved blue, Done green.

Each cycle the loop dispatches one worker per actionable story (up to 5 at once) and waits for all of them; the next cycle picks up their new columns. When only stories in your columns, or waiting on your answers, are left, it polls the board every 30 seconds and resumes the moment something changes.

## Questions

Agents ask questions as a comment on the story, with numbered questions each carrying a proposed default (reply "defaults ok" or answer some). The story gets `Waiting On: You` on the board and is skipped until you reply on the issue. Any new non-bot comment without the agent marker counts as a reply; the field clears itself and the worker resumes with the whole Q&A history. After 3 question rounds on one story the comment suggests rescoping it. Agents prefer writing assumptions into the plan (an explicit "Assumptions" section) over asking, so Plan Approval is where you review them.

## Sub-agent sizing

Workers rate each story low / medium / high on complexity and risk (auth, migrations, money, public APIs, infra, concurrency, irreversibility) and size planners, reviewers and implementers from it, e.g. 1 / 2 / 3-4 reviewers with different lenses. The rating is posted in the status comment. Whether a worker can spawn its own sub-agents depends on your Claude Code build; if it cannot, it does the same passes itself in sequence.

## The mod

Installing the plugin adds a band above the prompt while a loop is running (it hides itself when no worker is active and `state.json` has not been refreshed for 15 minutes):

```
📋 My Board  🙋 Plan Approval: 2  🚦 PR Approval: 1  ❓ Waiting on you: 1
🔨 Add CSV export
🔍 Fix login redirect
```

The board name is a link. Each worker row shows the emoji of the story's current column; it updates as the worker moves the story.

## Files

- `~/.config/claude-github-project/config.json`: board ids, field ids, repo to local-path map, settings.
- `~/.config/claude-github-project/state.json`: live state the mod reads (counts, waiting stories, workers).
- `~/.config/claude-github-project/worktrees/`: one git worktree per story (`cgp/<issue>-<slug>` branches).
- `~/.config/claude-github-project/plans/`: plan HTML sources (published as Claude artifacts).

Settings: `scripts/cgp config concurrency 3` (parallel workers, default 5), `scripts/cgp config pollSeconds 60`.

## CLI

`scripts/cgp --help` lists everything. Notable: `list` (board snapshot, also clears answered questions), `wait`, `move`, `set`, `ask`, `feedback`, `ci-wait`, `merge`, `merge-wait`, `worktree`.

## Develop

```bash
python3 -m unittest discover -s tests          # CLI against a fake gh
claude plugin test .                           # mod tests (hooks/*.test.ts); needs a Claude Code build that knows mods
```

Not covered by tests: the real GitHub GraphQL schema and a live agent run. Test against a throwaway board first.
