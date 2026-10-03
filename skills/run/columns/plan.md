# Column: Plan

The story was sent back, or a worker stopped mid-plan.

- **No `plan` link:** do the Todo steps from step 3 onward (the story is already in Plan).
- **Has a `plan` link:**
  1. Collect feedback: `CGP feedback <item>` plus the artifact's comments (ArtifactComments tool on the plan URL). Include `answers` if `answered`.
  2. Rate the story (rule 4). For each distinct problem or change request, spawn an agent to resolve it in the plan (several agents only if they touch separate sections). Questions in comments get answered in the plan or the status comment.
  3. Update and republish the artifact so it is current, and refresh `CGP touches <item> <paths...>` if the set of files changed. Reply to each artifact comment you addressed.
  4. Post a status comment listing what changed.
  5. `CGP move <item> plan_review`.
