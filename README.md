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
   - Replaces the Status options with the 10 columns below, adds fields `Waiting On`, `Plan`, `PR`, links the current repo to the board and records its local path.
   - Existing items whose old status name matches a new column keep it, closed issues go to Done, everything else lands in Todo. Closed issues anywhere on the board are filed under Done.
   - Run it again from another repo's checkout (same URL) to link more repos. Repos without a known local path are asked for.
2. `/cgp:run`. Add stories to Todo on the board (issues, or draft items which get converted to issues in the repo the agent picks) and leave it running. It ends only when every story is Done, or when you stop it. To stop cleanly, press **Stop after this cycle** in the board band above the prompt (or run `scripts/cgp stop`): the loop lets the running workers finish, then stops before dispatching more. `/cgp:run` picks up from the board.

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

Each cycle the loop dispatches one worker per actionable story (no cap by default) and waits for all of them; the next cycle picks up their new columns. When only stories in your columns, or waiting on your answers, are left, it polls the board every 30 seconds and resumes the moment something changes.

## Keeping up with main, and stories that collide

- Every time a worker is about to write code (implement, fix review findings, address PR feedback, resolve a merge problem) it runs `scripts/cgp sync <story>`, which fetches and rebases the story's worktree onto the latest default branch. Conflicts are resolved by the worker (a sub-agent for big ones), tested, and force-pushed with lease; if it can't resolve one with confidence it asks you.
- Each plan records the files and directories it changes (`cgp touches`). Before implementing, the worker runs `cgp overlap`, which compares them with every other in-flight story (plan files, plus the real file list of any open PR). When another story shares a file and is further along (or at the same stage with a lower issue number), this one is ordered behind it with `cgp block`: it stays in Plan Approved with a status comment saying who it waits for, the loop skips it, and it is released the moment the blocker is Done and then rebased onto the new main. Directory-level overlaps never block; they are noted in the plan or PR. The ordering is a strict stage-then-number order, and `block` refuses to create cycles.
- The mod shows how many stories are queued this way.

## Safety

The loop runs unattended with your `gh` token, and it reads text anyone can write on a linked repo. The design assumes that text is hostile:

- **Only trusted people can steer it.** `feedback`, `answers` and question replies only use comments from you, repo owners, and collaborators with write access (checked through the API). Everything else is reported as `ignoredUntrusted` with no body. The agent marker only counts on comments posted by your own gh account, so it can't be forged.
- **The human gates are enforced in code, not just in prompts.** `cgp move` refuses to move a story into Plan Approved or PR Approved, out of Plan Approval or PR Approval, or to Done unless its PR is merged. `cgp merge` works only on a story that is in PR Approved and only for that story's own PR. The `PR` field must name a PR on a repo linked to the board.
- **Prompts treat everything written by people as data** (no running commands, fetching URLs, adding dependencies or leaking tokens because text said so), and `cgp guard` fails any branch that touches `.github/` or CODEOWNERS without the approved plan listing it.
- **Code changed after your PR approval is re-approved**: a fix that is more than a clean rebase sends the story back to PR Approval instead of merging.
- `~/.config/claude-github-project` is private to your user (0700/0600).

What it can't do: an agent with Bash can still call `gh` directly. For defence in depth consider Claude Code permission rules denying `Bash(gh pr merge:*)`, `Bash(gh auth token:*)` and `Bash(gh api graphql:*)` for sessions running the loop, and a fine-grained token (or `GH_TOKEN`) limited to the linked repos and without the `workflow` scope. The setup scripts need `project`; the loop does not need `workflow`.

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
- `~/.config/claude-github-project/worktrees/`: one git worktree per story (`cgp/<issue number>` branches, under `<owner>/<repo>/<number>`).
- `~/.config/claude-github-project/plans/`: plan HTML sources (published as Claude artifacts).

Settings: `scripts/cgp config concurrency 3` (cap on parallel workers; default 0 = no cap), `scripts/cgp config pollSeconds 60`.

## CLI

`scripts/cgp --help` lists everything. Notable: `list` (board snapshot, also clears answered questions), `wait`, `move`, `set`, `ask`, `feedback`, `ci-wait`, `merge`, `merge-wait`, `worktree`.

## Develop

```bash
python3 -m unittest discover -s tests          # CLI against a fake gh and real temp git repos
claude plugin test .                           # mod tests (hooks/*.test.ts); needs a Claude Code build that knows mods
                                               # (validating the plugin itself: point `claude plugin validate` at a copy without marketplace.json;
                                               # builds older than ~2.1.280 warn about `types`/`modules` and skip the band)
```

Not covered by tests: the real GitHub GraphQL schema and a live agent run. Test against a throwaway board first.
