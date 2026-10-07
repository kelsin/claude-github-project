---
name: doctor
description: Use when cgp misbehaves, after installing it, or when the user asks to check or clean up the installation.
---

# cgp doctor

The CLI is `scripts/cgp` at the plugin root, two directories above this skill's base directory; run it as `python3 "<absolute path>/scripts/cgp"` (`python` on Windows), quoting the path. Run `python3 "<absolute path>/scripts/cgp" doctor` (add `--deep` to also read the board and find worker rows of stories that are gone) and report every ❌ and ⚠️ with the fix it prints (❌ fails the run, ⚠️ is advice). If it reports leftover session files, stale locks or worktrees of finished stories, offer `cgp gc --dry-run`, show what it would delete, and run `cgp gc` only after the user agrees.

If it reports a corrupt data file, cgp already set it aside as `<board>.data.json.corrupt-<time>` under `boards/` and restored the last good copy (or started empty): tell the user what it said, and leave the set-aside file for them to look at and delete.

If it reports orphan worker rows, or a story that is stuck after a crash, tell the user they can run `cgp unstick <item> --dry-run` (then without `--dry-run`; `--kill` also stops a worker that is still running) in a terminal. Never run `unstick` or `worktree-remove --discard` yourself: they are the user's.
