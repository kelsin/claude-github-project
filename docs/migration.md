# The ten-column layout

Early versions used ten columns: agent-side Plan Review and PR Review columns, and the user's columns called Plan Approval and PR Approval. Versions up to 0.1.x migrated such a board when `/cgp:run` started. Version 0.2 no longer does, and refuses to run on one (`cgp` stops with an error naming the board) rather than guess: its agent-side "Plan Review" and "PR Review" would otherwise be matched by name to the user's columns of the same names, and stories nobody reviewed would look approvable.

To bring such a board forward:

1. Install cgp 0.1.x, run `/cgp:run` once on the board (it migrates under the board's lock and reports what it moved), and stop it.
2. Update to the current version. Boards already on the eight-column layout are upgraded on first use: their saved config renames the column keys `plan_approval` / `pr_approval` to `plan_review` / `pr_review` (schema 3). Nothing changes on GitHub.

`cgp move` still accepts `plan_approval` and `pr_approval` as aliases for the new keys.
