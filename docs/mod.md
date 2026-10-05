# The mod

Installing the plugin adds a band above the prompt while a loop is running (it hides itself once the session's state has not been refreshed for 15 minutes, e.g. after `cgp release`). The mod is loaded in every session, but each session only shows its own state:

```
📋 My Board  🙋 Plan Review: 2  🚦 PR Review: 1  ❓ Waiting on you: 1
❓ Rename the export flag waiting on you
🔨 Add CSV export · implementing
🔍 Fix login redirect · reviewing (3 reviewers)
⏳ Bump the retry limit · waiting on CI
```

Each worker row shows what it is doing, set by the worker with `cgp worker phase <item> <planning|reviewing|revising|implementing|fixing|ci|merging> [detail]`; the CLI also sets phases by itself, so a worker that forgets still shows something true: planning or implementing from its column, reviewing once the plan is published or the PR is opened, ci while `ci-wait` runs (then the previous phase returns), merging during `merge`. Workers call `worker phase` for what the CLI cannot see (revising, fixing) and for the detail. A move resets the phase.

The board name and each waiting story are links (the story link opens the issue with the question). A toast appears when a story newly starts waiting on you; stories already waiting when the session starts are shown but not toasted.
