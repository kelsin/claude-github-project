# Column: Plan Approved

1. Rule 1. Run `CGP overlap <item>` (refresh `CGP touches` from the approved plan first if it is missing). If `suggest` is `block`: `CGP block <item> <other>` for each story listed, post a status comment saying which stories you wait on and which files overlap, and stop WITHOUT moving (the loop skips blocked stories and releases this one when they are Done). Otherwise continue.
2. `CGP move <item> implement`. `CGP worktree <item>`, then `CGP sync <item>` (rule 9). Read the approved plan artifact and any human comments on it.
3. Rate the story (rule 4). Spawn implementers by the plan's step list, splitting only into disjoint-file parts. Each writes code and tests, runs the repo's own test/lint commands, and commits. Run the full checks yourself in the worktree afterwards.
4. Push the branch and open the PR: `gh pr create -R <repo> --head <branch>` with a description that has what and why, a link to the plan, how it was tested, and `Closes <issueRepo>#<number>` (full `owner/repo#n` form when the repos differ). `CGP set <item> pr <pr url>`.
5. CI loop: `CGP ci-wait <repo> <pr number>`. On `red`, spawn an agent to fix from the returned logs, commit, push at once, and run `ci-wait` again. After 5 failed rounds, `CGP ask` for help and stop. On `green` or `none`, continue.
6. Post a status comment on the story (PR link, rating, what was tested).
7. `CGP move <item> pr_review`.
