# Column: Plan Approved

1. Rule 1. Run `CGP overlap <item>` (refresh `CGP touches` from the approved plan first if it is missing). If `suggest.action` is `declare`, run `CGP touches` from the plan and repeat. If it is `block`: `CGP block <item> <other>` for each story in `on` (if `block` refuses because of a cycle, the other story now ranks behind you: `CGP block <item> --unblock` and continue), post a status comment saying which stories you wait on and which files overlap, and stop WITHOUT moving (the loop skips blocked stories and releases this one when they are Done). Otherwise (`proceed`) run `CGP block <item> --unblock` in case an old block is left, and continue.
2. `CGP move <item> implement` (the story is in Plan Approved, which only the user sets, so this move is allowed from it). `CGP worktree <item>`, then `CGP sync <item>` (rule 7). Read the approved plan artifact and any human comments on it.
3. Rate the story (rule 4). Spawn implementers by the plan's step list, splitting only into disjoint-file parts. Each writes code and tests, runs the repo's own test/lint commands, and commits. Run the full checks yourself in the worktree afterwards.
4. Open the PR per rule 7, with a description that has what and why, a link to the plan, how it was tested, and `Closes <issueRepo>#<number>` (full `owner/repo#n` form when the repos differ).
5. CI loop: `CGP ci-wait <repo> <pr number>`. On `red`, spawn an agent to fix from the returned logs, commit, push at once, and run `ci-wait --sha <pushed sha>` again. After 5 failed rounds, `CGP ask` for help and stop. On `green` or `none`, continue.
6. Post a status comment on the story (PR link, rating, what was tested).
7. `CGP move <item> pr_review`.
