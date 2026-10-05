# Workflow

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

Planning and review share one worker run (as do implementing and PR review), so a story is picked up by a fresh worker only after Todo, Plan Approved and PR Approved. The mod shows which phase each worker is in (see [the mod](mod.md)).

## Dispatch

The loop dispatches one background worker per actionable story (no cap by default) and keeps watching the board: a story that arrives while others are mid-implementation is picked up on the next poll, and a finished worker's story is dispatched again in its new column. When several stories are ready to start and some touch the same files (per `cgp touches`), the loop starts the largest group that doesn't collide and holds the rest back until their rivals finish. `concurrency` caps workers in flight. When only stories in your columns, or waiting on your answers, are left, it polls the board every 30 seconds and resumes the moment something changes.

## Story fields

**Priority.** Set a story's `Priority` to High, Medium or Low and stories in the same column are dispatched in that order (unset after Low); `Hold` keeps a story from being dispatched at all. `scripts/cgp add "<title>" --priority High` creates an issue and puts it on the board; `scripts/cgp import <label>` adds existing labelled issues.

**Skip planning.** Type `Skip` in a Todo story's `Plan` field and no plan is made: the worker writes `- Plan: Skip` into the story description instead of a plan link and moves the story from Todo straight to Implement, working from the description and iterating there.

**Auto approve.** Set a story's `Auto Approve` field to `Plan`, `PR` or `Both` and the agent passes that gate itself: a planned story goes to Plan Approved (instead of Plan Review) and a story with a PR goes to PR Approved (instead of PR Review), each noted in its status comment. `cgp move` allows it only from Plan / Implement and only for the gate the field names; only you can set the field. With `PR` the code is never seen by you before merge, so the "changed after approval, send back to PR Review" rule doesn't apply to that story. `Skip` plus `Both` makes a story fully hands-off.

## Questions

Agents ask questions as a comment on the story, with numbered questions each carrying a proposed default (reply "defaults ok" or answer some). The story gets `Waiting On: You` on the board (a story queued behind another story shows `Waiting On: Another story` instead, set and cleared by `cgp list`) and is skipped until you reply on the issue. Any new non-bot comment without the agent marker counts as a reply; the field clears itself and the worker resumes with the whole Q&A history. When a 4th round of questions is asked on one story, the comment adds a note suggesting it be rescoped or split. Agents prefer writing assumptions into the plan (an explicit "Assumptions" section) over asking, so Plan Approval is where you review them.
