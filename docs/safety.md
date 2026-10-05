# Safety

The loop runs unattended with your `gh` token, and it reads text anyone can write on a linked repo. The design assumes that text is hostile:

- **Only trusted people can steer it.** `feedback`, `answers` and question replies only use comments from you, repo owners, and collaborators with write access (checked through the API). Everything else is reported as `ignoredUntrusted` with no body. The agent marker only counts on comments posted by your own gh account, so it can't be forged.
- **The human gates are enforced in code, not just in prompts.** `cgp move` refuses to move a story into Plan Approved or PR Approved, out of Plan Review or PR Review, or to Done unless its PR is merged. `cgp merge` works only on a story that is in PR Approved and only for that story's own PR. The `PR` field must name a PR on a repo linked to the board.
- **Why a custom `PR` field instead of GitHub's built-in "Linked pull requests"?** The built-in field is read-only through the API and anyone with write access to the issue can change it (keyword links and sidebar links), and it can hold several PRs, including cross-repo ones. `cgp merge` and Done must act on exactly one PR the loop itself opened, so only `cgp set` writes `PR`, and it validates the URL against the board's linked repos. Keyword auto-linking also only happens for PRs targeting the default branch and can lag PR creation.
- **Prompts treat everything written by people as data** (no running commands, fetching URLs, adding dependencies or leaking tokens because text said so), and `cgp guard` fails any branch that touches a guarded file (`.github/`, CODEOWNERS and more, see below) without the approved plan listing it.
- **Code changed after your PR approval is re-approved**: a fix that is more than a clean, conflict-free rebase (conflict resolution, CI-fix commits) sends the story back to PR Review instead of merging, and auto-merge is disarmed before any push.
- `~/.config/claude-github-project` is private to your user (0700/0600).

What it can't do: an agent with Bash can still call `gh` directly. For defence in depth consider Claude Code permission rules denying `Bash(gh pr merge:*)`, `Bash(gh auth token:*)` and `Bash(gh api graphql:*)` for sessions running the loop, and a fine-grained token (or `GH_TOKEN`) limited to the linked repos and without the `workflow` scope. The setup scripts need `project`; the loop does not need `workflow`.

## Merging only what you reviewed

When a worker moves a story to PR Review, `cgp move` records the PR's head commit. `cgp merge` and `cgp merge-wait` merge only that commit. If the head moved after review (a fix, a collaborator's push) they refuse (exit 7, or state `changed`) and the worker has to post what changed and send the story back to PR Review. Exceptions, both made by the tool itself: a clean rebase through `cgp sync` and the branch update `merge-wait` does when the PR is behind. A rebase that needed conflict resolution is never exempt. They also refuse a PR whose branch is not the story's `cgp/<number>` branch, a draft, and (when auto-merge is unavailable and the merge would happen at once) any PR whose checks are not green. A story approved before this check existed is taken as reviewed at its current head.

`cgp guard` fails a branch that changes guarded files the approved plan does not list: `.github/`, CODEOWNERS, `.npmrc`, `.yarnrc*`, `Makefile`, Dockerfiles, `.husky/`, `.pre-commit-config.yaml`, `.gitmodules`, `.claude/`, `.mcp.json`. Change the list with `cgp config guardFiles '<comma-separated globs>'`.

Story titles are text anyone can write. They never travel through a command line (`cgp worker start <item>` reads the title from the board) and the worker spawn prompt carries only ids; titles reach a worker through `cgp prepare`, as data.

`cgp doctor` checks whether the permission denies recommended above are present in your Claude Code settings.
