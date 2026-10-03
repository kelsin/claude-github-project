---
name: setup
description: Use when the user gives a GitHub Projects v2 board URL and wants it set up as the board for the current repository (columns, fields, repo links) so /cgp:run can work its stories.
---

# cgp setup

The CLI is `scripts/cgp` at the plugin root, two directories above this skill's base directory. Call it `CGP` below and always use its absolute path.

Input: a board URL (`https://github.com/orgs/<org>/projects/<n>` or `/users/<user>/projects/<n>`). If the user gave none, ask for it.

1. Find the current repo: `gh repo view --json nameWithOwner -q .nameWithOwner` and the main clone path (first `worktree` line of `git worktree list --porcelain`, so a linked worktree resolves to its main clone). If not in a git repo, skip `--repo`/`--repo-path` and ask which repo(s) to link instead.
2. Run `CGP setup <url> --repo <owner/name> --repo-path <toplevel>`.
   - Exit code 3 means the gh token lacks the `project` scope. Tell the user to run `gh auth refresh -s project` themselves (it is a browser step) and stop.
   - Other errors: show the message verbatim and stop.
3. Report what setup did: the 10 columns now on the board, the fields it added (`Waiting On`, `Plan`, `PR`, `Repo`), and `itemsRemapped`. Say plainly that existing items whose old status name did not match a new column went to Todo.
4. Run `CGP discover`. For every repo in `missing`, ask the user (AskUserQuestion, a deferred tool: load it with ToolSearch first) for its local clone path or whether to skip it, then `CGP repo-path <owner/name> <path>`. Repos with no local path cannot be implemented in; stories for them will ask the user.
5. Tell the user: to link more repos to this board, run `/cgp:setup <same url>` from another repo's checkout. Start working stories with `/cgp:run`.
