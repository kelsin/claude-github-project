---
name: status
description: Use when the user asks what the board is doing, what is waiting on them, or how /cgp:run is going.
---

# cgp status

The CLI is `scripts/cgp` at the plugin root, two directories above this skill's base directory; run it as `python3 "<absolute path>/scripts/cgp"` (`python` on Windows), quoting the path. Run `python3 "<absolute path>/scripts/cgp" status` and show the output as is. It is read-only (never run `cgp use` for it; if it asks which board, tell the user) and works from any session, running or not. Offer `/cgp:run` when the loop is not running and stories are actionable.

For the stories waiting on the user, oldest first with diff size and CI, `cgp review` (or `cgp review --html`) shows the digest.
