---
description: Commit the work so far as Conventional Commits — only your own files, never pushes
argument-hint: [optional scope or message hint]
---

Checkpoint the current work. $ARGUMENTS

1. **Branch** — if you are on `main`/`master`, create one first: `git switch -c <type>/<short-description>`.
2. **Scope** — only files *you* changed in this session. `git status --porcelain` will also show the user's work: leave it alone, and ask before committing a file that holds both.
3. **Green first** — the code imports, `mast check` has no high findings, and the tests covering it pass. Do not checkpoint a broken state.
4. **Split** — one logical change per commit. Stage explicit paths only: `git add -- <paths>`. Never `git add -A`.
5. **Message** — `<type>(<scope>): <imperative summary>`, ≤ 72 characters, no trailing period. A body explaining *why* when the reason is not obvious from the diff.
6. **Never push**, never `--amend` a pushed commit, never `--no-verify`.
7. Report the commits made (`git log --oneline -n <count>`) and anything left uncommitted, with the reason.
