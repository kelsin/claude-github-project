# Setup

`/cgp:setup https://github.com/orgs/<org>/projects/<n>` (or `/users/<user>/projects/<n>`), run from a checkout of one of your repos.

What it does:

- Replaces the Status options with the 8 [columns](workflow.md#columns). A board in the old ten-column layout is refused, see [migration](migration.md).
- Adds the fields `Waiting On`, `Plan`, `PR`, `Preview` (the Netlify deploy preview URL, copied from the PR by `cgp preview`), `Auto Approve` and `Priority` (see [workflow](workflow.md)). Run setup again on an existing board to get new fields.
- Links the current repo to the board (a repo can be on one board only) and records its local path.
- Adds three tabs if the board lacks them (an untouched starter "View 1" becomes the first): **Tasks** (table), **Board** (columns by Status) and **Approvals** (Status board filtered to Plan Review and PR Review). GitHub's API can't set a view's grouping or sort, so turn on group-by Repository in Board and Approvals yourself if you want it; existing views are never changed.

`/cgp:setup <url> --dry-run` shows what would change without touching the board. Existing items whose old status name matches a new column keep it, closed issues go to Done, everything else lands in Todo. Closed issues anywhere on the board are filed under Done.

Run it again from another repo's checkout (same URL) to link more repos. Repos without a known local path are asked for.

## Running and stopping

`/cgp:run` works the board until every story is Done or you stop it. To stop cleanly, press **Stop after this cycle** in the board band above the prompt, or run `scripts/cgp stop [board-url-or-key]` from a separate shell (the argument may be left out when exactly one live session holds a board lock, otherwise pass the board URL). The loop lets running workers finish, then stops before dispatching more. `/cgp:run` picks up from the board.

`/cgp:status` (or `scripts/cgp status`) shows the board at any time; `/cgp:doctor` checks that everything is in place.
