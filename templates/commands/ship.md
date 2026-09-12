---
description: Ship loop — QA + review until green, changelog, commits, and a PR draft ready for approval
---

Get the current work ready to ship — fast, and without cutting corners.

1. Run the `/qa` loop in full, including `mast check` and the reviewer until its verdict is `pass`.
2. **Changelog** — if shipped code changed and the repo has `CHANGELOG.md`, add user-facing bullets under `## [Unreleased]`.
3. **Migrations and config** — if the change needs a migration, a new environment variable or a deploy step, say so explicitly and make sure it is in the changelog and the PR body.
4. **Commit** — commit anything still uncommitted, following `/checkpoint`: only your files, Conventional Commits, one logical change per commit, on a non-protected branch.
5. **PR draft** — a title that is a Conventional Commit header (it becomes the squash commit) and a description: summary (what + why), how it was verified (the actual commands and their results), risks and rollback. Use `.github/pull_request_template.md` if it exists and tick only what was really done.
6. Show the user: the QA checklist, the review verdict, the branch's commits (`git log --oneline <base>..HEAD`) and the PR draft. **Ask before pushing or opening a PR** — never do either unprompted.
