---
name: doctor
description: Use when cgp misbehaves, after installing it, or when the user asks to check or clean up the installation.
---

# cgp doctor

The CLI is `scripts/cgp` at the plugin root, two directories above this skill's base directory. Run `<absolute path>/scripts/cgp doctor` and report every ❌ and ⚠️ with the fix it prints (❌ fails the run, ⚠️ is advice). If it reports leftover session files, stale locks or worktrees of finished stories, offer `cgp gc --dry-run`, show what it would delete, and run `cgp gc` only after the user agrees.
