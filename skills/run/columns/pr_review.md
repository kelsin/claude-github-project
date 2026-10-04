# Column: PR Review

1. Rule 1. `CGP merge <item> --cancel` (rule 7), then `CGP worktree <item>` and `CGP sync <item>` (rule 7, so reviewers see the diff against current main and fixes can be pushed) and size the change with `gh pr view <pr> -R <repo> --json additions,deletions,changedFiles` (reviewers read the full diff with `gh pr diff` themselves).
2. Rate the story (rule 4) and spawn reviewers by that rating, each with a distinct lens (correctness, security, tests, simplicity and reuse, performance, compatibility). They read the diff and the surrounding code and return concrete findings: severity, file:line, fix. They do not post to GitHub.
3. When all reviewers are done, fix every accepted finding at once: spawn separate fix agents (disjoint files in parallel), commit, push. Include any human feedback from rule 1. Rejected findings need a one-line reason in your status comment.
4. High risk only: spawn one more reviewer after the fixes to confirm none of the findings remain and nothing new broke.
5. `CGP ci-wait <repo> <pr> --sha <pushed sha>` and fix until `green` or `none` (max 5 rounds, then ask).
6. Post one comment on the PR (`CGP comment <item> --pr`) summarising what was reviewed and fixed, and a status comment on the story.
7. `CGP move <item> pr_approval`.
