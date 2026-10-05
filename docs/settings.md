# Settings

Show all with `scripts/cgp config`; set one with `scripts/cgp config <key> <value>`. Per board.

| Key | Default | Meaning |
|---|---|---|
| `concurrency` | 0 | cap on parallel workers (0 = no cap) |
| `pollSeconds` | 30 | how often the loop polls the board |
| `remoteControl` | 1 | `/cgp:run` turns on Remote Control for its session when it runs in the Claude desktop app, so you can follow and steer the loop from claude.ai or the phone; 0 skips that |
| `autoMigrate` | 1 | `/cgp:run` brings an old board's columns up to date ([migration](migration.md)) |
| `maxWorkerMinutes` | 240 | a worker running longer is reported as `stalled` by `cgp list` and released by the loop (0 = never) |
| `sharedFiles` | lockfiles, `*.schema.json`, locales, snapshots, `*.md` | comma-separated fnmatch globs for files many stories edit; overlaps on them are reported under `shared` instead of blocking; empty disables |
| `guardFiles` | `.github/*`, CODEOWNERS, `.npmrc`, ... | comma-separated globs of files an agent may change only when the approved plan lists them ([safety](safety.md)); empty disables |
