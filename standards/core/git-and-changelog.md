---
description: Commit discipline, branches and changelog entries
alwaysApply: true
---

# Git and changelog

## Commits

- Conventional Commits: `<type>(<scope>): <imperative summary>`, type one of `feat fix docs style refactor perf test build ci chore revert`. Header ≤ 72 characters, no trailing period.
- One logical change per commit. Formatting-only changes go in their own commit so review can see the real diff.
- Stage explicit paths. Never `git add -A`/`git commit -a` — it sweeps in the user's unrelated work and sometimes their secrets.
- Work on a branch named `<type>/<short-description>`. Never commit to `main`/`master`.
- Commit at every coherent, green step, and before handing a turn back, so work is never lost. Pushing is the user's call.
- The body explains *why* the change was made and anything a reviewer would otherwise have to ask. Reference the issue.
- Never `--no-verify`, never force-push a shared branch, never rewrite published history.

## Changelog

- `CHANGELOG.md` in Keep a Changelog form, newest first, under `## [Unreleased]`.
- One user-facing bullet per change that a user or operator would notice — behaviour, API surface, configuration, migration steps. Internal refactors do not need an entry.
- Write for the person upgrading: what changed, what they must do.
- Breaking changes are marked as such in both the changelog and the commit (`feat!:` or a `BREAKING CHANGE:` trailer), with the migration path spelled out.
