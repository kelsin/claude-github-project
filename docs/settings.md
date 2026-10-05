# Settings

Show all with `scripts/cgp config`; set one with `scripts/cgp config <key> <value>`. Per board.

| Key | Default | Meaning |
|---|---|---|
| `concurrency` | 0 | cap on parallel workers (0 = no cap) |
| `pollSeconds` | 30 | how often the loop polls the board |
| `remoteControl` | 1 | `/cgp:run` turns on Remote Control for its session when it runs in the Claude desktop app, so you can follow and steer the loop from claude.ai or the phone; 0 skips that |
| `maxWorkerMinutes` | 240 | a worker running longer is reported as `stalled` by `cgp list` and released by the loop (0 = never) |
| `sharedFiles` | lockfiles, `*.schema.json`, locales, snapshots, `*.md` | comma-separated fnmatch globs for files many stories edit; overlaps on them are reported under `shared` instead of blocking; empty disables |
| `guardFiles` | `.github/*`, CODEOWNERS, `.npmrc`, ... | comma-separated globs, added to the built-in list, of files an agent may change only when the approved plan lists them ([safety](safety.md)); the built-in list always applies, so empty adds nothing |
| `notifyCommand` | empty | a command run when a story starts waiting on you, enters a review column, is Done or stalls. The story is in the environment (`CGP_EVENT` = waiting / review / done / stalled, `CGP_TITLE`, `CGP_URL`, `CGP_BOARD`), never on the command line. The program must be an absolute path to an executable that you own and others cannot write, outside worktrees and `/tmp`; it runs without `GH_*` / `GITHUB_*` / `GIT_*` / `*_TOKEN` variables. Example: `cgp config notifyCommand "$HOME/bin/notify-cgp"`. Enabling it does not announce the stories already on the board |
| `previewProvider` | `netlify` | where deploy previews come from: `netlify`, `vercel`, `cloudflare` (each read from the provider's bot comment on the PR) or `deployments` (GitHub's Deployments API, no bot). A repo's `.cgp.json` can override it |
| `draftPRs` | 0 | 1: workers open PRs as drafts; moving the story to PR Review marks the PR ready |
| `plannerModel`, `reviewerModel`, `implementerModel` | empty | the model for that sub-agent role: `sonnet`, `opus`, `haiku` or `fable`, or a rating map such as `low:haiku,medium:sonnet,high:opus`. Empty: the session's model. Workers read it with `cgp models <rating>` |

## Per-repo settings: `.cgp.json`

A repo can carry a `.cgp.json` at the root of its default branch (never read from a story's own branch, so a story cannot loosen its own guard). `cgp repo-config <owner/name>` shows what cgp reads; `cgp prepare` hands it to the worker as `repoConfig`.

```json
{
  "test": "make test",
  "lint": "ruff check .",
  "sharedFiles": ["src/registry.py"],
  "guardFiles": ["deploy/*"],
  "reviewers": ["security", "accessibility"],
  "preview": { "provider": "vercel" }
}
```

- `test`, `lint`: the repo's own checks, which workers run before pushing.
- `sharedFiles`, `guardFiles`: added to the board's lists; a repo can add to a guard, never remove from it.
- `reviewers`: extra review lenses for the sub-agent reviewers.
- `preview`: `{"provider": ...}`, or `{"bot": "<login>", "pattern": "<regex, {pr} = PR number>"}` for a provider cgp does not know.

Unknown keys and values of the wrong type are ignored.
