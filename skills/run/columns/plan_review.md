# Column: Plan Review

1. Follow rule 1. Read the plan artifact (ArtifactComments and `CGP feedback` may hold feedback; apply it as in the Plan column).
2. Rate the story (rule 4) and spawn reviewers by that rating, each with a distinct lens: feasibility against the real code, risks and missed edge cases, test strategy, scope creep, security and migrations when risk is high. Each returns concrete findings: severity, where in the plan, the fix. Reviewers read the code through the worktree (`CGP worktree <item>`) to check the plan against reality.
2b. Run `CGP overlap <item>`. Give reviewers the result; if another story is changing the same files, the plan needs an Ordering note (who goes first, what to rebase on) and must not assume the other story's changes aren't coming.
3. When all reviewers are done, spawn agents to apply every accepted finding to the plan (one per independent section). Record each finding and its resolution in the Review log. Reject a finding only with a reason recorded there.
4. If a finding needs a decision only the user can make, put it in the plan's Open questions and also `CGP ask` with your proposed default, then stop without moving.
5. Make sure the published artifact is current, then post a status comment (review summary, rating, counts).
6. `CGP move <item> plan_approval`.
