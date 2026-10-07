# Changelog

## [0.6.0](https://github.com/kelsin/claude-github-project/compare/v0.5.0...v0.6.0) (2026-10-07)


### :tada: Features

* support native windows ([#67](https://github.com/kelsin/claude-github-project/issues/67)) ([515d7c9](https://github.com/kelsin/claude-github-project/commit/515d7c905628f9fffa66f6ffdc038d94c2c8c9a7))

## [0.5.0](https://github.com/kelsin/claude-github-project/compare/v0.4.2...v0.5.0) (2026-10-06)


### :tada: Features

* dispatch stories that unlock the most work first ([#57](https://github.com/kelsin/claude-github-project/issues/57)) ([789f0d8](https://github.com/kelsin/claude-github-project/commit/789f0d8a4e3380d83861ff07081d0d307ee70f1b))
* recover faster from daemon death, corrupt state and github errors ([#63](https://github.com/kelsin/claude-github-project/issues/63)) ([3cb9926](https://github.com/kelsin/claude-github-project/commit/3cb99267e2f643965b159b2ccf76baa8af00d039))


### :bug: Bug Fixes

* stop Todo and Plan stories stalling behind later columns ([#64](https://github.com/kelsin/claude-github-project/issues/64)) ([413339a](https://github.com/kelsin/claude-github-project/commit/413339a692922e6f3fb7fd90260c921bdf42c9af))


### :hammer_and_wrench: Code Refactoring

* remove duplicated helpers and dead code ([#58](https://github.com/kelsin/claude-github-project/issues/58)) ([e4d7b8e](https://github.com/kelsin/claude-github-project/commit/e4d7b8e616c24f7b590ca3b1768391504ee3e8c3))
* remove the daemon ([#65](https://github.com/kelsin/claude-github-project/issues/65)) ([a72bcc4](https://github.com/kelsin/claude-github-project/commit/a72bcc45d69c6daa34ec5b06395d2f241d52aa00))

## [0.4.2](https://github.com/kelsin/claude-github-project/compare/v0.4.1...v0.4.2) (2026-10-06)


### :bug: Bug Fixes

* show why an approved story is not starting and poll faster ([#51](https://github.com/kelsin/claude-github-project/issues/51)) ([b90040a](https://github.com/kelsin/claude-github-project/commit/b90040a12bc259cd35df7fad5af4e941cba19a54))
* stop the plan artifact watch after publishing ([#52](https://github.com/kelsin/claude-github-project/issues/52)) ([072897a](https://github.com/kelsin/claude-github-project/commit/072897ad1d34ce68d4eb49b742b8f5dd9966d951))

## [0.4.1](https://github.com/kelsin/claude-github-project/compare/v0.4.0...v0.4.1) (2026-10-05)


### :broom: Chores

* adopt release-please labels, PR title/header and changelog sections ([#44](https://github.com/kelsin/claude-github-project/issues/44)) ([fca7e0f](https://github.com/kelsin/claude-github-project/commit/fca7e0f9bd707cb6ddfdb46e7a75856ac9436537))
* change release-please PR header emoji to a clipboard ([#47](https://github.com/kelsin/claude-github-project/issues/47)) ([c456d2c](https://github.com/kelsin/claude-github-project/commit/c456d2cdcac06181273fd2d47adb199871ee7c7a))


### :traffic_light: Tests

* serialize fakegh calls so daemon tests don't race on its db ([#48](https://github.com/kelsin/claude-github-project/issues/48)) ([b49ef4d](https://github.com/kelsin/claude-github-project/commit/b49ef4d053c759714613bfe015b78db952d37d63))

## [0.4.0](https://github.com/kelsin/claude-github-project/compare/v0.3.0...v0.4.0) (2026-10-05)


### Features

* check Auto Approve live when moving into a review column ([#38](https://github.com/kelsin/claude-github-project/issues/38)) ([5c4f71c](https://github.com/kelsin/claude-github-project/commit/5c4f71c13e752dd46885cfaf9337f847b552cebd))
* native dependencies, policy auto-approval, sub-stories and daemon ([#41](https://github.com/kelsin/claude-github-project/issues/41)) ([06e1252](https://github.com/kelsin/claude-github-project/commit/06e1252fdaa5cc131f126ef8e31d956bccb8db18))

## [0.3.0](https://github.com/kelsin/claude-github-project/compare/v0.2.0...v0.3.0) (2026-10-05)


### Features

* update GitHub Actions to their latest major versions ([#34](https://github.com/kelsin/claude-github-project/issues/34)) ([f3b0bea](https://github.com/kelsin/claude-github-project/commit/f3b0beae794e5856bd52f69c02eb88d011151c5e))

## 0.2.0

Security
- `cgp worker start <item>` reads the story's title and column from the board; the title no longer travels through a shell command line, and the worker spawn prompt carries ids only.
- `cgp merge` / `merge-wait` merge only the commit the user reviewed (recorded when a story enters PR Review). Clean rebases and branch updates done by the tool are exempt; anything else exits 7 / state `changed`. They also refuse a PR whose branch is not `cgp/<number>`, and drafts, and (without auto-merge) PRs whose checks are not green.
- `cgp guard` covers more than `.github/` and CODEOWNERS and is configurable (`guardFiles`). The built-in list now always applies (the setting can only add to it, so emptying it no longer switches the guard off) and is extended; guarded paths are allowed only from the touches declared when the story entered Plan Approved.
- Merge gates: merges are pinned to the head commit that was read, refuse forks and non-default bases, and a missing review record is refused (a story dragged straight to PR Approved needs one pass through PR Review). `merge-wait` returns `revoked` and disarms auto-merge when the story leaves PR Approved. A clean rebase is exempt only when it rebased the reviewed commit.
- `notifyCommand` runs only when it is an absolute path to an executable you own, outside worktrees and `/tmp`, without token environment variables; `cgp repo-path` checks the clone's origin and never repoints; `cgp set` validates plan and PR links; `cgp prepare` reports `authorTrusted`; `cgp import` skips issues from untrusted authors.
- Workers older than `maxWorkerMinutes` are reported as `stalled` and released by the loop.

Fixes
- `/cgp:run` names the session after the board even when several boards are set up (`cgp use` returns `sessionTitle`, which the skill applies).
- `merge-wait` survives a transient `gh` failure like `ci-wait` does.
- Replies to a question are fetched since the question was posted instead of paging through every comment on every poll.
- `paths.json` is updated under the state lock; the lock is re-entrant.

Features
- Multi-board use: the board is chosen by the repo you run in (its `origin` remote, also from a worktree), so sessions in different repos work different boards without `cgp use`. A repo belongs to one board: setup refuses one another board has, reports `skippedRepos`, and `cgp doctor` flags duplicates. Bare terminals on different boards no longer share state.
- `Priority` field (High / Medium / Low / Hold) orders dispatch within a column; Hold is never dispatched. `cgp add` / `cgp import` (and `/cgp:add`) put stories on the board from the terminal.
- `notifyCommand` hook; `.cgp.json` per-repo settings (`cgp repo-config`); preview providers Netlify, Vercel, Cloudflare Pages and the Deployments API; `plannerModel` / `reviewerModel` / `implementerModel` with `cgp models`; `draftPRs`.

New
- `cgp setup` puts Priority first after Title in the Tasks view, also on an existing board (`viewsUpdated`, dry run `viewsToUpdate`); nothing else in the view changes.
- `cgp status` (human-readable, read-only), `cgp doctor`, `cgp gc`, `cgp setup --dry-run`; skills `/cgp:status`, `/cgp:stop`, `/cgp:doctor`.
- `docs/` (architecture, safety, settings, migration, troubleshooting, generated CLI reference); LICENSE.

Internal
- `scripts/cgp` is a shim over the `scripts/cgp_lib` package; commands are one table (`cli.COMMANDS`) with help text.
- `cgp prepare` runs its parts in-process instead of starting five Python processes.
- Shared item GraphQL fragment, `issue_item`, `advance_cursor`, `strip_id`; ruff also checks bugbear rules for Python 3.8.
- Polling loops share `util.Poll`.
- CI runs the suite on Python 3.8 and the latest 3.x.

Breaking
- The user's columns are now `plan_review` / `pr_review` (config schema 3; existing eight-column configs are renamed on first use, `cgp move` accepts the old names). The ten-column layout is no longer migrated: `cgp` refuses such a board and points at [docs/migration.md](docs/migration.md). `cgp migrate` and the `autoMigrate` setting are gone, as are the `plan_review.md` / `pr_review.md` prompt shims and the single-board `config.json` import.
- The mod takes column emoji and phase labels from `meta` in the session state file instead of its own copy.

## 0.1.4

Earlier versions: see the git history.
