# Column: Plan Approved

1. Rule 1, then `CGP move <item> implement`.
2. `CGP worktree <item>`. Read the approved plan artifact and any human comments on it.
3. Rate the story (rule 4). Spawn implementers by the plan's step list, splitting only into disjoint-file parts. Each writes code and tests, runs the repo's own test/lint commands, and commits. Run the full checks yourself in the worktree afterwards.
4. Push the branch and open the PR: `gh pr create -R <repo> --head <branch>` with a description that has what and why, a link to the plan, how it was tested, and `Closes <issueRepo>#<number>` (full `owner/repo#n` form when the repos differ). `CGP set <item> pr <pr url>`.
5. CI loop: `CGP ci-wait <repo> <pr number>`. On `red`, spawn an agent to fix from the returned logs, commit, push at once, and run `ci-wait` again. After 5 failed rounds, `CGP ask` for help and stop. On `green` or `none`, continue.
6. Post a status comment on the story (PR link, rating, what was tested).
7. `CGP move <item> pr_review`.
