# Changelog

## [0.10.0](https://github.com/kelsin/claude-github-project/compare/v0.9.0...v0.10.0) (2026-10-17)


### :tada: Features

* **defer:** review deferred items before filing them ([#144](https://github.com/kelsin/claude-github-project/issues/144)) ([0711fcf](https://github.com/kelsin/claude-github-project/commit/0711fcf7800c384e3057393760d9fe640f19befe))
* **guard:** report unprintable lookalikes with a reason ([#157](https://github.com/kelsin/claude-github-project/issues/157)) ([fd32e0c](https://github.com/kelsin/claude-github-project/commit/fd32e0c52e8e7f66a232085cb97ea19b243ba517))
* let a plan directory cover files the globs allow ([#129](https://github.com/kelsin/claude-github-project/issues/129)) ([7ac9e91](https://github.com/kelsin/claude-github-project/commit/7ac9e919102b4e5a80a547be31975c5cd851d70b))
* **policy:** allow joiner and direction marks in file names for auto-approval ([#132](https://github.com/kelsin/claude-github-project/issues/132)) ([5d903ad](https://github.com/kelsin/claude-github-project/commit/5d903ad6546eed16c9aa3ec2c3f48b702748029e))
* **policy:** allow ZWNJ in file names ([#140](https://github.com/kelsin/claude-github-project/issues/140)) ([39d8ed0](https://github.com/kelsin/claude-github-project/commit/39d8ed0bbedc1f55deff9d2fdad934259ca3b3c3))
* **policy:** refuse remaining default-ignorable characters in file names ([#156](https://github.com/kelsin/claude-github-project/issues/156)) ([d60808b](https://github.com/kelsin/claude-github-project/commit/d60808b535f8bae95cb369b534592ab2ef8d6db9))
* **reevaluate:** add cgp reevaluate sweep for PR Review stories ([#126](https://github.com/kelsin/claude-github-project/issues/126)) ([dd3b5d4](https://github.com/kelsin/claude-github-project/commit/dd3b5d4d40b671e4962a78396cd41df7363323ad))
* **reevaluate:** pass through stderr of stories that succeed ([#139](https://github.com/kelsin/claude-github-project/issues/139)) ([39e0606](https://github.com/kelsin/claude-github-project/commit/39e0606930c01e2009a5619f779ba6abab1d5337))
* **run:** research ready-check questions in plan instead of asking in todo ([#145](https://github.com/kelsin/claude-github-project/issues/145)) ([c44694c](https://github.com/kelsin/claude-github-project/commit/c44694c28a22227db3e6bbd8b1626819b2469a22))
* **wait:** report why cgp wait returned ([#122](https://github.com/kelsin/claude-github-project/issues/122)) ([8c131cd](https://github.com/kelsin/claude-github-project/commit/8c131cdcca4ed4c8778989c6d73eb6f70cdd4a66))


### :bug: Bug Fixes

* **guard:** strip every unprintable character when matching guarded paths ([#151](https://github.com/kelsin/claude-github-project/issues/151)) ([1cea813](https://github.com/kelsin/claude-github-project/commit/1cea813d1d3caa60da4d7707aba0ac4fd7f79b43))
* **policy:** re-raise RateLimited so a rate limit in policy evaluation stops the sweep ([#142](https://github.com/kelsin/claude-github-project/issues/142)) ([19f83d6](https://github.com/kelsin/claude-github-project/commit/19f83d693bce3403e8f1bb5e665234794b7ec688))
* **policy:** record and post auto-approve refusals ([#118](https://github.com/kelsin/claude-github-project/issues/118)) ([d18fb93](https://github.com/kelsin/claude-github-project/commit/d18fb93f6e19af0c7078b47baa66c0c66f79ca63))
* **policy:** refuse control characters in file names for auto-approval ([#123](https://github.com/kelsin/claude-github-project/issues/123)) ([ffb4ab4](https://github.com/kelsin/claude-github-project/commit/ffb4ab422396a1211d4339278299f944c93a016f))
* **policy:** refuse more invisible characters in file names ([#146](https://github.com/kelsin/claude-github-project/issues/146)) ([dc20a14](https://github.com/kelsin/claude-github-project/commit/dc20a1471b9b12be8d5fbee9fb5a9a429e202ce4))
* **policy:** refuse more invisible characters in file names ([#153](https://github.com/kelsin/claude-github-project/issues/153)) ([f9051bb](https://github.com/kelsin/claude-github-project/commit/f9051bbc6e45970d32221bac6ce929f92ccfa8ab))
* re-raise rate limits from author_trusted and the stack gate read ([#149](https://github.com/kelsin/claude-github-project/issues/149)) ([fa54166](https://github.com/kelsin/claude-github-project/commit/fa54166a74d412b691b33476a53b2f7b346adb9e))
* **reevaluate:** exit 1 when stories errored and stop on a rate limit ([#134](https://github.com/kelsin/claude-github-project/issues/134)) ([aaeed11](https://github.com/kelsin/claude-github-project/commit/aaeed110c63d30244d388a3d86fc0ff83e867583))
* **split:** name the real reason in the guarded-file error ([#130](https://github.com/kelsin/claude-github-project/issues/130)) ([ad224ec](https://github.com/kelsin/claude-github-project/commit/ad224ecaf59d622c52eeac38760ef130a318d5f6))
* **split:** refuse non-plain paths even when the plan lists them ([#138](https://github.com/kelsin/claude-github-project/issues/138)) ([23ed63c](https://github.com/kelsin/claude-github-project/commit/23ed63c0e941e003f4767240cb39e110db81ac59))
* **trust:** a rate limit on the permission lookup raises instead of caching 'untrusted' ([#155](https://github.com/kelsin/claude-github-project/issues/155)) ([b8134b5](https://github.com/kelsin/claude-github-project/commit/b8134b5daabc21401a1b68de776645e8e603614c))


### :book: Documentation

* **run:** remove duplicated stopDrafts bullet ([#117](https://github.com/kelsin/claude-github-project/issues/117)) ([4cb35da](https://github.com/kelsin/claude-github-project/commit/4cb35da7363effd1255605fc5295c5fde5dde5d5))
* **run:** tell the loop to wait again on an idle result ([#112](https://github.com/kelsin/claude-github-project/issues/112)) ([e5c86c0](https://github.com/kelsin/claude-github-project/commit/e5c86c051b8b7e9dee69164bd676dfc27b218091))

## [0.9.0](https://github.com/kelsin/claude-github-project/compare/v0.8.0...v0.9.0) (2026-10-13)


### :tada: Features

* **add:** summarize the input into the issue title ([#108](https://github.com/kelsin/claude-github-project/issues/108)) ([84675ca](https://github.com/kelsin/claude-github-project/commit/84675ca7d0ca1b84cfcec4de96f7fedf535a4016))


### :bug: Bug Fixes

* **defer:** store filed records under their own data key ([#110](https://github.com/kelsin/claude-github-project/issues/110)) ([fa367e2](https://github.com/kelsin/claude-github-project/commit/fa367e26b8001a05ee37958718b9162b36f8be58))

## [0.8.0](https://github.com/kelsin/claude-github-project/compare/v0.7.0...v0.8.0) (2026-10-10)


### :tada: Features

* add a review packet to every PR ([#96](https://github.com/kelsin/claude-github-project/issues/96)) ([a383abb](https://github.com/kelsin/claude-github-project/commit/a383abb22f16cceb2fac47803e32ed3ee20367df))
* add cgp review digest and nag reminders ([#97](https://github.com/kelsin/claude-github-project/issues/97)) ([776b414](https://github.com/kelsin/claude-github-project/commit/776b414176fa9a9cb7e6645da09b62d6541b31c6))
* add cgp stats command for cycle-time numbers ([#107](https://github.com/kelsin/claude-github-project/issues/107)) ([5a633bb](https://github.com/kelsin/claude-github-project/commit/5a633bbb06a087311e3a7f5f9946a90ce737f5a7))
* add ci-triage to classify red ci before fixing ([#100](https://github.com/kelsin/claude-github-project/issues/100)) ([6d87b91](https://github.com/kelsin/claude-github-project/commit/6d87b91cf100e6dc082724a6e46f5afa3bb4297d))
* add failing-main and dependency intake ([#90](https://github.com/kelsin/claude-github-project/issues/90)) ([0168c43](https://github.com/kelsin/claude-github-project/commit/0168c4343729d7b017dbb58de1b9dfbcadc3bcd1))
* add human-only cgp approve command ([#98](https://github.com/kelsin/claude-github-project/issues/98)) ([e19fa88](https://github.com/kelsin/claude-github-project/commit/e19fa88a9b91de967415029b76e877487b8c416d))
* add repo brief for workers ([#104](https://github.com/kelsin/claude-github-project/issues/104)) ([6b67275](https://github.com/kelsin/claude-github-project/commit/6b6727520b0c0ab69dea714ac07e684e9ad5f7ef))
* add speculative implementation of low-risk stories ([#106](https://github.com/kelsin/claude-github-project/issues/106)) ([c60d215](https://github.com/kelsin/claude-github-project/commit/c60d2159eca0c94a6085aec1f0eaf8fb1b81b98e))
* check a story is ready before planning it ([#92](https://github.com/kelsin/claude-github-project/issues/92)) ([d737157](https://github.com/kelsin/claude-github-project/commit/d7371573dd287a853ed8c8baff542924f92af2d3))
* file deferred work from merged PRs as held stories ([#91](https://github.com/kelsin/claude-github-project/issues/91)) ([b329fec](https://github.com/kelsin/claude-github-project/commit/b329fec56c314f15e9643afccf49be39052dca26))
* merge approved PRs as a train ([#105](https://github.com/kelsin/claude-github-project/issues/105)) ([3cb3653](https://github.com/kelsin/claude-github-project/commit/3cb36539a2adf1f1806c0853e178af7afe23bbdc))
* propose house rules from review history ([#101](https://github.com/kelsin/claude-github-project/issues/101)) ([aa22313](https://github.com/kelsin/claude-github-project/commit/aa2231332cfe06898df0e0cf6aec26d921220e2f))
* resume a story's worker on send-back ([#102](https://github.com/kelsin/claude-github-project/issues/102)) ([46dc3a6](https://github.com/kelsin/claude-github-project/commit/46dc3a628dc3b9cba331689dc940614cc369d98d))
* start a story on its blocker open pr branch ([#103](https://github.com/kelsin/claude-github-project/issues/103)) ([7561568](https://github.com/kelsin/claude-github-project/commit/7561568a10252e294ed7cd77a2a1c98ce144d0dd))


### :bug: Bug Fixes

* remove worktrees of merged stories and say why one is kept ([#94](https://github.com/kelsin/claude-github-project/issues/94)) ([30fb732](https://github.com/kelsin/claude-github-project/commit/30fb73269f35922b549c7e2624ae1cde381493bd))


### :book: Documentation

* put a human summary first in the plan page ([#99](https://github.com/kelsin/claude-github-project/issues/99)) ([1117128](https://github.com/kelsin/claude-github-project/commit/111712882468e7de7c8a26b16719118206c43225))

## [0.7.0](https://github.com/kelsin/claude-github-project/compare/v0.6.0...v0.7.0) (2026-10-08)


### :tada: Features

* remove worktrees of finished stories automatically ([#71](https://github.com/kelsin/claude-github-project/issues/71)) ([81c8931](https://github.com/kelsin/claude-github-project/commit/81c893189fae67b0c815c9d15dea101201d72b59))


### :bug: Bug Fixes

* do not count unseen sub-stories as done when closing a split parent ([#73](https://github.com/kelsin/claude-github-project/issues/73)) ([077a093](https://github.com/kelsin/claude-github-project/commit/077a093b7da2880ba9ddfe9aff411dddc166c5bb))

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
