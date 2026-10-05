---
name: add
description: Use when the user wants to add a story to the board from the terminal, or to import existing issues by label.
---

# cgp add

The CLI is `scripts/cgp` at the plugin root, two directories above this skill's base directory.

- One story: `<absolute path>/scripts/cgp add "$(cat <title file>)" [--repo <owner/name>] [--priority High|Medium|Low|Hold] --body -` with the description on stdin (heredoc). It creates the issue and puts it on the board in Todo. `--repo` may be left out when the board has one linked repo; otherwise ask which. Report the issue URL.
- Existing issues: `<absolute path>/scripts/cgp import <label> [--repo <owner/name>]` puts every open issue with that label that is not on the board yet into Todo.
- If the CLI says the board has no Priority field, tell the user to run `/cgp:setup <board-url>` again.

The title and description are the user's own words; pass them as given. Write the title to a file with the Write tool and let the shell read it with `"$(cat <title file>)"` (as above) instead of typing it into the command line, so quotes and `$(...)` in it are never parsed as shell. Do not start `/cgp:run` unless asked.
