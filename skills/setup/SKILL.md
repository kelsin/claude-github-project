---
name: setup
description: Use when the user gives a GitHub Projects v2 board URL and wants it set up as the board for the current repository (columns, fields, repo links) so /cgp:run can work its stories.
---

# cgp setup

The CLI is `scripts/cgp` at the plugin root, two directories above this skill's base directory. Call it `CGP` below and always use its absolute path.

Input: a board URL (`https://github.com/orgs/<org>/projects/<n>` or `/users/<user>/projects/<n>`). If the user gave none, ask for it.

1. Find the current repo: `gh repo view --json nameWithOwner -q .nameWithOwner` and the main clone path (first `worktree` line of `git worktree list --porcelain`, so a linked worktree resolves to its main clone). If not in a git repo, omit `--repo`/`--repo-path` from the command in step 2, and ask which repo(s) to link instead; run `CGP setup <url> --repo <answer>` with the user's answer (plus `--repo-path <path>` if they give a local clone).
2. Setup rewrites the board's Status options. If the board already has stories, first run `CGP setup <url> --repo <owner/name> --dry-run`, show the user what it reports (`columns`, `fieldsToAdd`, `viewsToAdd`) and ask before going on. Then run `CGP setup <url> --repo <owner/name> --repo-path <main clone path>`.
   - Exit code 3 means the gh token lacks the `project` scope. Tell the user to run `gh auth refresh -s project` themselves (it is a browser step) and stop.
   - Other errors: show the message verbatim and stop.
3. Report what setup did: the 8 columns now on the board (a board still in the old ten-column layout is refused with an error pointing at docs/migration.md), the fields it added (`Waiting On`, `Plan`, `PR`, `Preview`, `Auto Approve`), and `itemsRemapped`. `viewsCreated` lists the tabs it added (Tasks, Board, Approvals; existing ones are left alone). Tell the user the API cannot set grouping, so group-by Repository on Board and Approvals is theirs to turn on in the GitHub UI. Say plainly that existing items whose old status name matched a new column kept it, closed issues went to Done, and everything else went to Todo.
4. Run `CGP discover`. For every repo in `missing`, ask the user (AskUserQuestion if available, else ask in chat) for its local clone path or whether to skip it, then `CGP repo-path <owner/name> <path>`. Repos with no local path cannot be implemented in; stories for them will ask the user.
5. Boards are independent: setting up another board keeps the others, and repo clone paths are shared between them. Tell the user: to link more repos to this board, run `/cgp:setup <same url>` from another repo's checkout. Start working stories with `/cgp:run`; `/cgp:doctor` checks the installation and `/cgp:status` shows the board.
