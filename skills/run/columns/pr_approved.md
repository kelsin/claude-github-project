# Column: PR Approved

The user approved the diff as it stands. Note `headRefOid` from `CGP pr-state <repo> <pr>` now.

1. If `pr-state` says `MERGED`, go to step 3. Otherwise `CGP merge <item>` (squash, auto-merge, falls back to a direct merge). If `requested` is false it often just means checks are still pending: go to step 2, which merges when the PR turns clean. Only act on the error text if `merge-wait` also reports a problem; if only the user can fix it (review required, no merge permission), `CGP ask` and stop.
2. `CGP merge-wait <item>` and handle its `state`:
   - `merged`: go to step 3.
   - `conflict` or `BEHIND`: `CGP worktree <item>`, `CGP sync <item>` (rule 7: resolves, tests, force-pushes), then `ci-wait`, `merge` and `merge-wait` again.
   - `ci-red`: fix as in the Implement column (`ci-wait` gives logs), push, then `merge-wait` again.
   - `blocked` or `closed`: `CGP ask` explaining why and stop.
   - `pending`: run `merge-wait` again (rule 7 caps this).
   Give up after 5 fix rounds with `CGP ask`.
   **Re-approval.** If you pushed anything other than a conflict-free rebase or a CI-only fix (the PR's `headRefOid` changed because of code you wrote), the user never approved that diff: post a status comment saying what changed and `CGP move <item> pr_approval` instead of merging.
3. Once merged: `CGP worktree-remove <item>`, post a status comment (merged, PR link), `CGP move <item> done`.
