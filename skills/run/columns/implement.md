# Column: Implement

The story was sent back from review, or a worker stopped mid-implementation.

- **No `pr` link:** do the Plan Approved steps from step 2 onward (the story is already in Implement; run `CGP overlap` first as in Plan Approved step 1; a block recorded here is honored by the loop).
- **Has a `pr` link:**
  1. `CGP merge <item> --cancel` (rule 7), `CGP worktree <item>`, `CGP sync <item>`, and `CGP feedback <item>` (issue comments, PR comments, inline review comments, requested-changes reviews). Include `answers` if `answered`.
  2. Rate the story (rule 4). Spawn agents to resolve each distinct request or issue (parallel only for disjoint files). Also resolve any CI failure on the PR.
  3. Commit and push. `CGP ci-wait <repo> <pr> --sha <pushed sha>` and fix until `green` or `none` (max 5 rounds, then ask), then `CGP preview <item>`.
  4. Reply on the PR to each addressed comment with `CGP comment <item> --pr` (one summary comment is fine), then a status comment on the story.
  5. `CGP move <item> pr_review`.
