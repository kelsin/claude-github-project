---
name: stop
description: Use when the user wants to stop the /cgp:run loop cleanly (finish running workers, dispatch nothing new).
---

# cgp stop

The CLI is `scripts/cgp` at the plugin root, two directories above this skill's base directory. Run `<absolute path>/scripts/cgp stop [board-url]`. Without an argument it stops this session's loop if this session holds a board's lock, else the one live session holding a lock when exactly one exists; with several, ask which board and pass its URL. Running workers finish first; the loop then stops itself. `stop --cancel` withdraws the request. Report the JSON result in one line.
