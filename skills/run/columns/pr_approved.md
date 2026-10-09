# Column: PR Approved

The user approved the diff as it stands. Run `CGP feedback <item>` (act on anything new as rule 1 says), and note `headRefOid` from `CGP pr-state <repo> <pr>` now.

`CGP worker phase <item> merging` when you start. Before you stop, `git rebase --abort` any rebase you left in progress and leave `git status` clean in the worktree.

1. If `pr-state` says `MERGED`, go to step 3. Otherwise run `CGP ci-wait <repo> <pr>` first; only when it is `green` or `none` and no code push is pending, `CGP merge <item>` (squash, auto-merge, falls back to a direct merge). If `requested` is false it often just means checks are still pending, or `train` lists the approved PRs that merge first (the merge train, rule 14): go to step 2, which merges when the PR turns clean. Only act on the error text if `merge-wait` also reports a problem; if only the user can fix it (review required, no merge permission), `CGP ask` and stop.
2. `CGP merge-wait <item>` (it also updates a behind branch itself) and handle its `state`:
   - `merged`: go to step 3.
   - `revoked`: the user took the approval back. Do nothing more and stop (the story is theirs again).
   - `conflict`: `CGP merge <item> --cancel`, `CGP worktree <item>`, `CGP sync <item>`. If it returns `clean` or `rebased` (a conflict-free rebase), `CGP guard <item>`, push with `--force-with-lease`, then `ci-wait --sha <pushed sha>`, `merge` and `merge-wait` again. If you had to resolve conflicts, follow the re-approval rule (rule 7).
   - `ci-red`: `CGP merge <item> --cancel`, run `CGP ci-triage <repo> <pr>` and fix as in the Implement column only when it says `real`, push. Your fix commit is code the user never approved: follow the re-approval rule (rule 7).
   - `changed` (or `merge`/`merge-wait` exiting 7): the PR head is not the commit the user reviewed. `CGP merge <item> --cancel`, post a status comment saying what changed, `CGP move <item> pr_review` (rule 7, re-approval).
   - `blocked` or `closed`: `CGP ask` explaining why and stop.
   - `pending`: run `merge-wait` again (rule 7 caps this). With an `ahead` list the PR is waiting for its turn in the merge train: it does not count toward the cap of 3, and `merge-wait` has changed nothing on the PR; run it again.
   - `dequeued`: the repo's merge queue no longer holds the PR (it was removed after a failure on the merge group, or never added). `CGP merge <item> --cancel`, `ci-triage` as for `ci-red`, then `CGP merge <item>` again once checks are green.
   - `merge` or `merge-wait` refusing because the story is stacked (rule 7, Stacked story): `CGP sync <item>` and go by its `state`: `rebased`: the CLI sent the story back to Implement for a new review (whether it retargeted the PR after its blocker merged or followed the blocker's branch), reply `blocked: rebased, back for review`; `clean`: the blocker is not merged yet, reply `waiting: blocker PR not merged`; `blocker-closed` or `tainted`: follow `shared.md` (`CGP ask`, reply `blocked`).
   Give up after 5 fix rounds with `CGP ask`.
3. Once merged (`CGP move <item> done` is a no-op if the story is already Done; treat "already done" as success): `CGP worktree-remove <item>`, run `CGP defer <item>` (files the PR description's `## Deferred` entries as stories on hold; safe to rerun; an error goes into the status comment and never blocks Done), post a status comment (merged, PR link, the stories `defer` filed), `CGP move <item> done`.
