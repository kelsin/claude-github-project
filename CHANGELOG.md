# Changelog

## 0.2.0

Security
- `cgp worker start <item>` reads the story's title and column from the board; the title no longer travels through a shell command line, and the worker spawn prompt carries ids only.
- `cgp merge` / `merge-wait` merge only the commit the user reviewed (recorded when a story enters PR Review). Clean rebases and branch updates done by the tool are exempt; anything else exits 7 / state `changed`. They also refuse a PR whose branch is not `cgp/<number>`, and drafts, and (without auto-merge) PRs whose checks are not green.
- `cgp guard` covers more than `.github/` and CODEOWNERS and is configurable (`guardFiles`).
- Workers older than `maxWorkerMinutes` are reported as `stalled` and released by the loop.

Fixes
- `merge-wait` survives a transient `gh` failure like `ci-wait` does.
- Replies to a question are fetched since the question was posted instead of paging through every comment on every poll.
- `paths.json` is updated under the state lock; the lock is re-entrant.

New
- `cgp status` (human-readable, read-only), `cgp doctor`, `cgp gc`, `cgp setup --dry-run`; skills `/cgp:status`, `/cgp:stop`, `/cgp:doctor`.
- `docs/` (architecture, safety, settings, migration, troubleshooting, generated CLI reference); LICENSE.

Internal
- `scripts/cgp` is a shim over the `scripts/cgp_lib` package; commands are one table (`cli.COMMANDS`) with help text.
- `cgp prepare` runs its parts in-process instead of starting five Python processes.
- Shared item GraphQL fragment, `issue_item`, `advance_cursor`, `strip_id`; ruff also checks bugbear rules for Python 3.8.
- CI runs the suite on Python 3.8 and the latest 3.x.

Planned: the legacy ten-column migration code is removed in 0.4 ([docs/migration.md](docs/migration.md)).

## 0.1.4

Earlier versions: see the git history.
