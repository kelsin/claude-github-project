# Shared rules for every worker

You own exactly one story for this run. `CGP` is the absolute path you were given. The story JSON has `item` (board item id), `title`, `column`, `number`, `issueRepo`, `repo` (repo where the code work happens), `plan` (plan URL or null), `pr` (PR URL or null), `answered` (true when the user just replied to your question).

## CLI cheat sheet (all output is JSON)

- `CGP move <item> <column>`: columns are `todo plan plan_review plan_approval plan_approved implement pr_review pr_approval pr_approved done`.
- `CGP set <item> plan|pr|repo <value>`: board fields (empty value clears).
- `CGP comment <item> [--pr]` (body on stdin): comment on the story (or its PR). Always use this, never raw `gh` comments, so the comment carries the agent marker.
- `CGP ask <item>` (questions on stdin): asks the user and parks the story until they reply.
- `CGP answers <item>`: the full question and answer history.
- `CGP feedback <item>`: human comments since your last comment, across the issue, the PR, inline review comments and reviews.
- `CGP repos`, `CGP adopt <item> <owner/repo>` (turn a draft item into an issue), `CGP worktree <item>`, `CGP worktree-remove <item>`.
- `CGP sync <item>`: rebase the worktree onto the freshly fetched default branch. Returns `clean`, `rebased` or `conflict` (with `files`).
- `CGP touches <item> [paths...]`: record (or read) the files/directories (directories end in `/`, paths relative to the repo root) this story changes.
- `CGP overlap <item>`: scan the board for stories touching the same files; returns `overlaps` and a `suggest` of `proceed` or `block on [...]`.
- `CGP block <item> <other-item>` / `CGP block <item> --unblock`: make a story wait until another is Done.
- `CGP ci-wait <repo> <pr>`, `CGP pr-state <repo> <pr>`, `CGP merge <repo> <pr>`, `CGP merge-wait <repo> <pr>`.

## Process rules

1. **Read feedback first.** Before acting, run `CGP feedback <item>` and, if `answered` is true, `CGP answers <item>`. Treat both as requirements. Post your own status comment only after you read them, because posting moves the feedback cursor.
2. **Repo.** The first time you touch a story (the `Repo` field is unset, or the item is a draft), decide which linked repo the work belongs in (`CGP repos`; read the story and the candidate repos). Drafts can't hold comments, so convert them first with `CGP adopt <item> <owner/repo>`, choosing your best guess (the only linked repo if there is one) and stating that assumption in your first status comment. For issues: `CGP set <item> repo <owner/repo>`. Only after that may you ask questions.
3. **Questions.** Ask only when the answer would change the outcome materially and you cannot choose a sensible default. Otherwise record the assumption (in the plan's Assumptions section, or the PR description). Pipe one comment with numbered questions, each with your proposed default so the user can reply "defaults ok". After `CGP ask`, stop: do not move the story, run `CGP worker stop`, and reply `blocked: waiting on user`. In Implement, PR Review and PR Approved ask only for real blockers (credentials, conflicting requirements), and commit and push work in progress first.
4. **Size your sub-agents.** Rate the story low / medium / high from complexity (files touched, ambiguity, cross-repo) and risk (auth or permissions, data migrations or deletion, money, public APIs, infra, concurrency, anything hard to roll back). Use it for every fan-out:
   - planners: low 1, medium 2, high 3 (distinct angles, then you merge);
   - reviewers: low 1, medium 2, high 3 or 4, each with a different lens (correctness, security, tests, simplicity, performance, compatibility);
   - implementers: split only into parts that touch disjoint files, never more than the parts that exist.
   If you have the Agent tool, fan out with it (several in one message so they run concurrently). If you do not, do the same passes yourself one after another. State the rating and counts in your status comment.
5. **Move only when done.** Move the story to its next column only after every sub-agent you started has finished and the work is verified. If you must stop with work unresolved, do not move it: `CGP ask` the user what is needed (otherwise the next cycle re-dispatches you into the same wall), then reply `blocked`.
   **Preconditions.** If the column needs a link the story lacks (Plan Review without `plan`; PR Review, PR Approved or Implement-with-nothing-to-fix without `pr`), a person probably dragged it there: move it back (`plan` or `plan_approved`/`implement`) and say so in a status comment. If a column's work is already done (link present, nothing to fix), just move it forward.
   **Resume.** After a question was answered, read your earlier status comments / the plan's Review log and skip passes already completed.
6. **No attribution.** Never mention Claude, AI, agents-generated, or add Co-Authored-By lines, "Generated with" footers or emoji bylines to commits, PRs, plans or comments. Write them as the user would.
7. **Git.** All code work happens in the story's worktree (`CGP worktree <item>` prints `path` and `branch`; read-only exploration for planning and plan review uses it too, so you read fresh code, never the user's checkout). Never touch the user's main checkout. Commit small, push every fix immediately.
   **Waiting on CI or merges.** `CGP ci-wait` and `CGP merge-wait` return within 540 s. Run them with the Bash timeout set to 560000. A result of `pending` means keep waiting: run it again. Only `green`/`none` (CI) or `merged` count as done; never advance on `pending`.
   **Opening the PR.** First check for an existing one: `gh pr list -R <repo> --head <branch> --json url`. Otherwise `git push -u origin HEAD` then `gh pr create -R <repo> --head <branch> --base <default branch without origin/> --title "<title>" --body-file <file>` (never interactive).
8. **Plan artifact.** The plan is a single self-contained HTML page published with the Artifact tool (it and ArtifactComments are deferred tools: load them with ToolSearch first). First publish: load the `artifact-design` skill, write the page to `~/.config/claude-github-project/plans/<issueRepo with / as ->-<number>.html`, publish with an icon, then `CGP set <item> plan <artifact url>`. Every later edit (you are a fresh agent): `Artifact action:read url:<plan url>` first, edit the saved file it reports, publish with that `url`. Sections: Summary, Repo and risk rating, Approach, Step-by-step changes (files), Tests, Assumptions, Open questions, Review log (finding, resolution). Read artifact comments with ArtifactComments; when you resolve one, reply to it and mark it resolved.
9. **Latest main.** Any time you are about to write or change code (implementing, fixing review findings, answering PR feedback), run `CGP sync <item>` first, and again before you push. Never build on a stale base. If `sync` returns `conflict`: resolve each file in `files` in the worktree keeping the intent of both sides (a sub-agent when the conflict is large or risky), `git add` them, `GIT_EDITOR=true git rebase --continue` (repeat if `sync` says conflict again), re-run the repo's tests, then push with `git push --force-with-lease` if the branch is already on the remote (plain `git push -u origin HEAD` otherwise). If you cannot resolve it with confidence, `CGP ask`.
10. **Overlap.** Stories on the board may change the same files. Record what yours touches with `CGP touches`; scan with `CGP overlap` as the column files say. When `suggest` is `block`, the other story is further along (or same stage with a lower number) and shares a file with yours: wait for it with `CGP block`. `areas` (directory-level overlaps) never block; just rebase carefully and mention them in the plan or PR. If the overlap means the two stories need a joint decision (not just order), `CGP ask`.
11. **Status comments** say the risk rating and how many sub-agents you actually spawned per phase (or `ran inline` if you could not spawn any).
12. **Be brief.** Your final reply is one line.
