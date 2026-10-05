# Column: Todo

1. Follow shared rule 2 (repo; drafts are adopted first). If you must ask, ask and stop.
2. **`skipPlan` is true** (the user typed `Skip` in the Plan field: the story is simple and is iterated on during implementation): no plan is made. `CGP set <item> plan Skip` (this writes `- Plan: Skip` into the story description in place of a plan link), `CGP move <item> implement`, then continue with `implement.md` (the same folder), the no-`pr` path, from step 1. The story description and its comments are the requirements; record your assumptions in the PR description.
3. Otherwise `CGP move <item> plan`.
4. The story is now in Plan and has no plan yet: continue with `plan.md` (the same folder) from step 1.
