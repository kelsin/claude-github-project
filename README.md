# claude-github-project (`cgp`)

A Claude Code plugin that turns a GitHub Projects v2 board into an agent pipeline. Stories move through planning, plan review, implementation, PR review and merge; you only approve plans and PRs.

It has three parts:

- **Skills**: `/cgp:setup` configures a board, `/cgp:run` runs the loop, `/cgp:add` puts a story on the board (or imports issues by label), `/cgp:status` shows the board, `/cgp:stop` stops the loop cleanly, `/cgp:doctor` checks the installation.
- **`scripts/cgp`**: a Python CLI doing every deterministic step (GraphQL, comments, CI polling, merging, worktrees), so agents only make decisions. Python 3.8+ and `gh` are the only requirements; macOS and Linux (WSL on Windows).
- **A mod**: a band above the prompt showing the board link, what is waiting on you, and one line per active worker ([details](docs/mod.md)).

## Install

```bash
claude plugin marketplace add kelsin/claude-github-project
claude plugin install cgp@claude-github-project
```

(From a local checkout: `claude plugin marketplace add ~/src/claude-github-project`.) Then restart Claude Code (or reload plugins). Your `gh` token needs the `project` scope; if it is missing, setup tells you to run:

```bash
gh auth refresh -s project
```

## Use

1. From a checkout of one of your repos: `/cgp:setup https://github.com/orgs/<org>/projects/<n>` (or `/users/<user>/projects/<n>`).
   - Replaces the Status options with the 8 columns below (a board in the old ten-column layout is refused, see [migration](docs/migration.md)), adds fields `Waiting On`, `Plan`, `PR`, `Preview` (the Netlify deploy preview URL, copied from the PR by `cgp preview`) `Auto Approve` and `Priority` (see below; run setup again on an existing board to get it), links the current repo to the board and records its local path. It also adds three tabs if the board lacks them (an untouched starter "View 1" becomes the first): **Tasks** (table), **Board** (columns by Status) and **Approvals** (Status board filtered to Plan Review and PR Review). GitHub's API can't set a view's grouping or sort, so turn on group-by Repository in Board and Approvals yourself if you want it; existing views are never changed.
   - `/cgp:setup <url> --dry-run` shows what would change without touching the board. Existing items whose old status name matches a new column keep it, closed issues go to Done, everything else lands in Todo. Closed issues anywhere on the board are filed under Done.
   - Run it again from another repo's checkout (same URL) to link more repos. Repos without a known local path are asked for.
2. `/cgp:run`. Add stories to Todo on the board (issues, or draft items which get converted to issues in the repo the agent picks) and leave it running. It ends only when every story is Done, or when you stop it. To stop cleanly, press **Stop after this cycle** in the board band above the prompt (or run `scripts/cgp stop [board-url-or-key]` from a separate shell; the argument may be left out when exactly one live session holds a board lock, otherwise pass the board URL): the loop lets the running workers finish, then stops before dispatching more. `/cgp:run` picks up from the board.

`/cgp:status` (or `scripts/cgp status`) shows the board at any time; `/cgp:doctor` checks that everything is in place.

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

**Priority.** Set a story's `Priority` to High, Medium or Low and stories in the same column are dispatched in that order (unset after Low); `Hold` keeps a story from being dispatched at all. `scripts/cgp add "<title>" --priority High` creates an issue and puts it on the board; `scripts/cgp import <label>` adds existing labelled issues.

**Skip planning.** Type `Skip` in a Todo story's `Plan` field and no plan is made: the worker writes `- Plan: Skip` into the story description instead of a plan link and moves the story from Todo straight to Implement, working from the description and iterating there.

**Auto approve.** Set a story's `Auto Approve` field to `Plan`, `PR` or `Both` and the agent passes that gate itself: a planned story goes to Plan Approved (instead of Plan Review) and a story with a PR goes to PR Approved (instead of PR Review), each noted in its status comment. `cgp move` allows it only from Plan / Implement and only for the gate the field names; only you can set the field. With `PR` the code is never seen by you before merge, so the "changed after approval, send back to PR Review" rule doesn't apply to that story. `Skip` plus `Both` makes a story fully hands-off.

Planning and review share one worker run (as do implementing and PR review), so a story is picked up by a fresh worker only after Todo, Plan Approved and PR Approved. The mod shows which phase each worker is in (see [the mod](docs/mod.md)).

The loop dispatches one background worker per actionable story (no cap by default) and keeps watching the board: a story that arrives while others are mid-implementation is picked up on the next poll, and a finished worker's story is dispatched again in its new column. When several stories are ready to start and some touch the same files (per `cgp touches`), the loop starts the largest group that doesn't collide and holds the rest back until their rivals finish, instead of spending workers on stories that would only block. `concurrency` caps workers in flight. When only stories in your columns, or waiting on your answers, are left, it polls the board every 30 seconds and resumes the moment something changes.

## Questions

Agents ask questions as a comment on the story, with numbered questions each carrying a proposed default (reply "defaults ok" or answer some). The story gets `Waiting On: You` on the board (a story queued behind another story shows `Waiting On: Another story` instead, set and cleared by `cgp list`) and is skipped until you reply on the issue. Any new non-bot comment without the agent marker counts as a reply; the field clears itself and the worker resumes with the whole Q&A history. When a 4th round of questions is asked on one story, the comment adds a note suggesting it be rescoped or split. Agents prefer writing assumptions into the plan (an explicit "Assumptions" section) over asking, so Plan Approval is where you review them.

## More

- [Safety](docs/safety.md): the threat model, the human gates, review-bound merging, recommended permission rules
- [Architecture](docs/architecture.md): code layout, files, parallel sessions, stories that collide, sub-agent sizing
- [Settings](docs/settings.md) and the [CLI reference](docs/cli.md)
- [The mod](docs/mod.md), [the ten-column layout](docs/migration.md), [troubleshooting](docs/troubleshooting.md)
- [Changelog](CHANGELOG.md)

## Develop

```bash
python3 -m unittest discover -s tests          # CLI against a fake gh and real temp git repos
python3 -m ruff check scripts tests            # lint (ruff.toml)
scripts/gen-cli-docs                           # regenerate docs/cli.md after changing the command table
claude plugin test .                           # mod tests (hooks/*.test.ts); needs a Claude Code build that knows mods
                                               # (validating the plugin itself: point `claude plugin validate` at a copy without marketplace.json;
                                               # builds older than ~2.1.280 warn about `types`/`modules` and skip the band)
```

The unit tests use a fake gh, so the real GraphQL schema is not covered. `CGP_LIVE_TEST=1 CGP_LIVE_BOARD=<board url> python3 tests/live_smoke.py` runs the read-only commands against a throwaway board with your real token. A live agent run is not covered either: try a throwaway board first.

## License

MIT, see [LICENSE](LICENSE).
