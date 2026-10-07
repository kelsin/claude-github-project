# Settings

Show all with `scripts/cgp config`; set one with `scripts/cgp config <key> <value>`. Per board.

| Key | Default | Meaning |
|---|---|---|
| `concurrency` | 0 | cap on parallel workers (0 = no cap) |
| `pollSeconds` | 15 | how often the loop polls the board. Each poll reads the board (GraphQL) plus one REST comments call per story waiting on `You`, so 15 s is 240 polls an hour; the REST budget is the one at risk, because of the comments call per story waiting on `You` (10 s with many waiting stories approaches the 5000 requests/hour limit), and workers share the token's budgets. `cgp setup` and `cgp doctor` move a stored 30 (the old default) to 15; set your own with `cgp config pollSeconds N` |
| `remoteControl` | 1 | `/cgp:run` turns on Remote Control for its session when it runs in the Claude desktop app, so you can follow and steer the loop from claude.ai or the phone; 0 skips that |
| `maxWorkerMinutes` | 240 | a worker running longer is reported as `stalled` by `cgp list` and released by the loop (0 = never) |
| `nativeDependencies` | 1 | `on` / `off`: GitHub's own "blocked by" issue dependencies also hold a story back (see [architecture](architecture.md)); `off` leaves only cgp's blocks |
| `autoApprove` | `plan:never,pr:never` | the policy that lets agents pass a human gate for a low-risk story: `plan:low,pr:never` style, the highest rating the agent may give a story for that gate (`never`, `low`, `medium`, `high`; a gate not named stays `never`). Off by default. **Only you can change it**, in a terminal: `cgp config` refuses it inside a Claude session or without a terminal, notifies you when it changes, and `cgp status` and `cgp doctor` show it. See [safety](safety.md) for everything the policy also checks |
| `autoApproveFiles` | `docs/**/*.md`, `*.md` | comma-separated globs every file of an auto-approved story must match. `*` and `?` stay inside one path segment (`*.md` is root-level files only), `**` is any number of segments, case is ignored. Tests are not in the default because CI runs them: add them only if you accept that. Same human-only rule as `autoApprove`; no `..` or absolute paths. A built-in list is always refused whatever you set (`CLAUDE.md`, `AGENTS.md`, `SKILL.md`, `skills/`, column prompts, docs build config), and so is every guarded file |
| `sharedFiles` | lockfiles, `*.schema.json`, locales, snapshots, `*.md` | comma-separated fnmatch globs for files many stories edit; overlaps on them are reported under `shared` instead of blocking; empty disables |
| `guardFiles` | `.github/*`, CODEOWNERS, `.npmrc`, ... | comma-separated globs, added to the built-in list, of files an agent may change only when the approved plan lists them ([safety](safety.md)); the built-in list always applies, so empty adds nothing |
| `notifyCommand` | empty | a command run when a story starts waiting on you, enters a review column, is Done or stalls, or when the auto-approval policy is changed. The story is in the environment (`CGP_EVENT` = waiting / review / done / stalled / policy, `CGP_TITLE`, `CGP_URL`, `CGP_BOARD`), never on the command line. Not available on Windows. The program must be an absolute path to an executable that you own and others cannot write, outside worktrees, the cgp home and `/tmp` (so root-owned system tools are refused); it runs without `GH_*` / `GITHUB_*` / `GIT_*` / `*_TOKEN` variables. Example: `cgp config notifyCommand "$HOME/bin/notify-cgp"`. Enabling it does not announce the stories already on the board |
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

## Windows

Two things are unavailable on native Windows: killing orphaned worker processes (a worker the loop released is not terminated, only its story is) and `notifyCommand` (it is ignored). Everything else works the same.
