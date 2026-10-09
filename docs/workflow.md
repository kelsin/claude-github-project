# Workflow

## Columns

| Column | Who acts | What happens |
|---|---|---|
| 🆕 Todo | agents | Pick repo, check the story is ready (questions if not), move to Plan |
| 🧠 Plan | agents | Planners write an HTML plan artifact linked on the story, reviewers review it, updaters apply findings, move to Plan Review. Also where a story lands when you send it back (or it stalled): comments on the plan and the story are resolved, the changes re-reviewed |
| 🙋 Plan Review | **you** | Read the plan artifact. Comment and move to Plan, or move to Plan Approved |
| ✅ Plan Approved | agents | Move to Implement |
| 🔨 Implement | agents | Implement in a worktree, open PR, reviewers review it while CI runs, fixers fix, CI green, move to PR Review. Also where a story lands when you send it back: requested changes on the PR are fixed and re-reviewed |
| 🚦 PR Review | **you** | Review the PR. Comment and move to Implement, or move to PR Approved |
| 🚀 PR Approved | agents | Squash merge (auto-merge once CI is green), fix conflicts and CI until merged, move to Done |
| 🎉 Done | nobody | |

You can also pass the gate from a terminal with `cgp approve <item>` (the item is the project item id): it moves a story from Plan Review to Plan Approved or from PR Review to PR Approved. It must be run by you in a real terminal, outside a Claude session; a notification action that runs it needs a real terminal too, and wrapping it in a pty is not a supported route. For a PR it approves only the commit recorded when the story entered PR Review, or a clean, conflict-free rebase of it made by cgp. It refuses a PR with no such record or with any other head, and prints the commit it approved. A plan has no pin, so it prints the plan URL to check.

Colors: Todo blue, Plan yellow, Plan Review purple, Plan Approved blue, Implement red, PR Review purple, PR Approved blue, Done green.

Planning and review share one worker run (as do implementing and PR review), so a story is picked up by a fresh worker only after Todo, Plan Approved and PR Approved. The mod shows which phase each worker is in (see [the mod](mod.md)).

## Dispatch

The loop dispatches one background worker per actionable story (no cap by default) and keeps watching the board: a story that arrives while others are mid-implementation is picked up on the next poll, and a finished worker's story is dispatched again in its new column. When several stories are ready to start and some touch the same files (per `cgp touches`), the loop starts the largest group that doesn't collide and holds the rest back until their rivals finish (with no `concurrency` cap this grouping takes precedence over unlock order). `concurrency` caps workers in flight. While a Todo (not skip-plan) or Plan story is waiting and no Todo/Plan worker runs, later columns may use at most `concurrency` minus one slots, so planning never starves (a cap of 1 reserves nothing). A story in any column that is not starting because it is queued behind the cap, or (Plan Approved, Implement without a PR, skip-plan Todo) held back for a file overlap, shows `Waiting On: Another story` on the board (cleared once it starts); `cgp status` says which. When only stories in your columns, or waiting on your answers, are left, it polls the board every 15 seconds (`pollSeconds`) and resumes the moment something changes.

## Red CI

When a PR's checks fail, the worker runs `cgp ci-triage <repo> <pr>` before touching code. It compares the failed jobs with the PR's base branch and reruns the failed jobs once (at most twice per PR, once per commit). Verdicts: `flaky` (green after the rerun), `main-broken` (the base branch fails the same jobs; the worker waits instead of fixing), `real` (still red: the worker fixes it). A flaky job is counted per repo and job name and filed once as a Todo story on Hold with the `cgp-flaky` label; later flakes only add a count and an occasional comment. Release the story from Hold when you want it fixed.

## Automatic intake

Each snapshot of the loop (`cgp list` / `cgp wait`) can run an opt-in intake pass per repo (`intake` in `.cgp.json`, see [settings](settings.md)), at most every `intakeSeconds`. It files a Todo story when the default branch is red, and puts open Dependabot / Renovate PRs into PR Review as `Plan: Skip` stories for you to merge by hand; no worker is dispatched for them. A story it creates is picked up by the next cycle.

## House rules

A repo can carry a `.cgp-rules.md` at the root of its default branch: free-text house rules (style and conventions) that `cgp prepare` returns as `houseRules` and workers paste into planner, implementer and reviewer prompts (reviewers check the diff against it). Only the default branch is read, so a story cannot change its own rules, and a newly committed file shows up after the clone is synced (`origin/HEAD` is read locally). It is cut to 8,192 characters and stripped of control and bidi characters.

`cgp rules propose [--repo owner/name] [--limit 50] [--min 3] [--out PATH]` writes a proposal from your review history: it reads comments and reviews on the last merged `cgp/<n>` PRs (one list call, at most 100 PRs), keeps only those from trusted people (you, owners, collaborators with write access; no bots, no agent comments), groups similar ones by shared words, and keeps points seen at least `--min` times in at least 2 PRs. The result goes to `proposals/<owner>__<repo>/cgp-rules.proposed.md` in the cgp home, never into the repo: your existing rules plus one bullet per new point with the PR numbers it came from. Diff it, edit it and commit `.cgp-rules.md` to the default branch yourself; cgp never commits, pushes or opens a PR for it. `cgp status` shows a line while a proposal differs from the default branch. The loop runs it for each repo in `rulesDue` every `rulesProposeDays` (default 7, 0 = off); every run counts, including ones that found nothing.

## Story fields

**Review digest.** `scripts/cgp review` lists every story waiting on you (plan review, PR review, or a question), oldest wait first and smallest diff first within the same hour, with rating, files, CI state and links; `--html [PATH]` writes it as one self-contained page. It is read-only. The wait is counted from when the loop first saw the story in its column, so stories already there when the loop first ran are marked approximate (`~`) and listed last.

**Dependencies.** A story that GitHub shows as "blocked by" another issue on the board waits for it (set `nativeDependencies` to `off` to ignore GitHub's dependencies). `scripts/cgp status` lists each GitHub blocker with who opened it, and a dependency cycle that only you can break is asked about on the story.

**Priority.** Set a story's `Priority` to High, Medium or Low and stories in the same column are dispatched in that order (unset after Low), and within a column and priority the stories that unlock the most other stories (directly or through a chain of dependencies) go first; `Hold` keeps a story from being dispatched at all. `scripts/cgp add "Fix login" --priority High` creates an issue and puts it on the board; `scripts/cgp import <label>` adds existing labelled issues.

**Skip planning.** Type `Skip` in a Todo story's `Plan` field and no plan is made: the worker writes `- Plan: Skip` into the story description instead of a plan link and moves the story from Todo straight to Implement, working from the description and iterating there. Skip stories bypass the ready check.

**Auto approve.** Set a story's `Auto Approve` field to `Plan`, `PR` or `Both` and the agent passes that gate itself: a planned story goes to Plan Approved (instead of Plan Review) and a story with a PR goes to PR Approved (instead of PR Review), each noted in its status comment. `cgp move` allows it only from Plan / Implement and only for the gate the field names; only you can set the field. With `PR` the code is never seen by you before merge, so the "changed after approval, send back to PR Review" rule doesn't apply to that story. The field is re-read when the story reaches a review column: `cgp move` into Plan Review / PR Review lands in Plan Approved / PR Approved if you set the field while the agent was working. A story you sent back from PR Review to Implement with `Auto Approve` = `PR` still set is re-approved at its next move to PR Review. `Skip` plus `Both` makes a story fully hands-off.

**Auto-approval policy.** Per board, instead of per story, `cgp config autoApprove plan:low,pr:low` (run by you in a terminal) lets the agent pass a gate for low-risk, docs-only stories (`autoApproveFiles`). The worker rates the story with `cgp rate` and then makes its usual move into Plan Review or PR Review; `cgp move` lands it in the approved column instead when the policy says yes, and posts a comment saying why. Otherwise nothing changes: the story waits for you, and the move's `policy.reason` says what stopped it. The rules are in [safety](safety.md). Unlike the field, the policy does not delegate the merge: a PR it approved is held to the commit it checked.

**Deferred work.** Unfixed review nits and out-of-scope items are listed in a `## Deferred` section of the PR description (`- title :: reason`). After the PR merges, the worker runs `cgp defer <story>`, which files each (at most 10, once each) as a Todo story with Priority Hold in the same repo, pointing back to the parent. Nothing starts until you release a story from Hold.

**Sub-stories.** A plan for a large story may declare up to 10 sub-stories (`cgp split <story> --declare`). When you approve the plan, the worker runs `cgp split <story>`, which creates them in Todo with their plan skipped (the files each may change come from the approved declaration) and orders them as declared (as GitHub dependencies when possible, else in cgp). The parent waits (`Waiting On: Another story`) and is closed once every sub-story is Done by a merged PR. See [Safety](safety.md).

## Questions

Agents ask questions as a comment on the story, with numbered questions each carrying a proposed default (reply "defaults ok" or answer some). The story gets `Waiting On: You` on the board (a story queued behind another story shows `Waiting On: Another story` instead, set and cleared by `cgp list`) and is skipped until you reply on the issue. Any new non-bot comment without the agent marker counts as a reply; the field clears itself and the worker resumes with the whole Q&A history. When a 4th round of questions is asked on one story, the comment adds a note suggesting it be rescoped or split. Agents prefer writing assumptions into the plan (an explicit "Assumptions" section) over asking, so Plan Approval is where you review them. The ready check at Todo: a cheap low-rating reviewer asks, at most once per story, when the story lacks acceptance criteria, a clear repo, or looks like a duplicate or too big for one PR.
