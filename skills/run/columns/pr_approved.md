# Column: PR Approved

The user approved the diff as it stands. Run `CGP feedback <item>` (act on anything new as rule 1 says), and note `headRefOid` from `CGP pr-state <repo> <pr>` now.

`CGP worker phase <item> merging` when you start.

1. If `pr-state` says `MERGED`, go to step 3. Otherwise run `CGP ci-wait <repo> <pr>` first; only when it is `green` or `none` and no code push is pending, `CGP merge <item>` (squash, auto-merge, falls back to a direct merge). If `requested` is false it often just means checks are still pending: go to step 2, which merges when the PR turns clean. Only act on the error text if `merge-wait` also reports a problem; if only the user can fix it (review required, no merge permission), `CGP ask` and stop.
2. `CGP merge-wait <item>` (it also updates a behind branch itself) and handle its `state`:
   - `merged`: go to step 3.
   - `revoked`: the user took the approval back. Do nothing more and stop (the story is theirs again).
   - `conflict`: `CGP merge <item> --cancel`, `CGP worktree <item>`, `CGP sync <item>`. If it returns `clean` or `rebased` (a conflict-free rebase), `CGP guard <item>`, push with `--force-with-lease`, then `ci-wait --sha <pushed sha>`, `merge` and `merge-wait` again. If you had to resolve conflicts, follow the re-approval rule (rule 7).
   - `ci-red`: `CGP merge <item> --cancel`, fix as in the Implement column (`ci-wait` gives logs), push. Your fix commit is code the user never approved: follow the re-approval rule (rule 7).
   - `changed` (or `merge`/`merge-wait` exiting 7): the PR head is not the commit the user reviewed. `CGP merge <item> --cancel`, post a status comment saying what changed, `CGP move <item> pr_review` (rule 7, re-approval).
   - `blocked` or `closed`: `CGP ask` explaining why and stop.
   - `pending`: run `merge-wait` again (rule 7 caps this).
   Give up after 5 fix rounds with `CGP ask`.
3. Once merged (`CGP move <item> done` is a no-op if the story is already Done; treat "already done" as success): `CGP worktree-remove <item>`, post a status comment (merged, PR link), `CGP move <item> done`.
