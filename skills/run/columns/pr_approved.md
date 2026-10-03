# Column: PR Approved

1. `CGP pr-state <repo> <pr number>`: if `state` is `MERGED`, go to step 3. Otherwise `CGP merge <repo> <pr number>` (squash, auto-merge, falls back to a direct merge). If `requested` is false it often just means checks are still pending: go to step 2, which merges when the PR turns clean. Only act on the error text if `merge-wait` also reports a problem; if only the user can fix it (review required, no merge permission), `CGP ask` and stop.
2. `CGP merge-wait <repo> <pr number>` and handle its `state`:
   - `merged`: go to step 3.
   - `conflict`: in the worktree (`CGP worktree <item>`), merge the default branch, resolve conflicts, run the checks, push, `ci-wait`, then `merge` and `merge-wait` again.
   - `ci-red`: fix as in the Implement column (`ci-wait` gives logs), push, then `merge-wait` again.
   - `blocked` or `closed`: `CGP ask` explaining why and stop.
   - `pending`: run `merge-wait` again.
   Give up after 5 fix rounds with `CGP ask`.
3. Once merged: `CGP worktree-remove <item>`, post a status comment (merged, PR link), `CGP move <item> done`.
