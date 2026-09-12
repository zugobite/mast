# Commit discipline

Claude commits its own work as it goes, so a long session cannot lose it, and so the history reads as
a sequence of deliberate changes. It never pushes.

## The policy

- **Branch first.** Committing on `main`/`master` is refused. `git switch -c <type>/<short-description>`.
- **Conventional Commits.** `<type>(<scope>): <imperative summary>`, type one of `feat fix docs style refactor perf test build ci chore revert`, header ≤ 72 characters, no trailing period. A non-conforming header is refused before the commit runs.
- **Explicit paths only.** `git add -A`, `git add .` and `git commit -a` are blocked — they sweep in your unrelated work, and sometimes your secrets. Claude stages the paths it changed.
- **Green before committing.** The code imports, the analyzer has no high findings, the tests covering it pass.
- **One logical change per commit.** Formatting-only changes go in their own commit.
- **Never push.** Pushing to `main`, force-pushing and `--no-verify` are blocked; everything else is your call.

## When it commits

| Moment | What happens |
| --- | --- |
| mid-work | once ~`commits.checkpointLines` (150) lines of Claude's own work are uncommitted, it is reminded — once per commit, so it cannot nag |
| end of turn | when every other gate is green, uncommitted files Claude changed this turn must be committed |
| on request | `/checkpoint` |

## Configuration

```json
"commits": {
  "enabled": true,
  "types": ["feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert"],
  "maxHeaderLength": 72,
  "protectedBranches": ["main", "master", "release/*"],
  "checkpointLines": 150,
  "commitOnTurnEnd": true
}
```

| Key | Meaning |
| --- | --- |
| `enabled` | `false` removes commit enforcement and the commit gate entirely |
| `types` | allowed Conventional Commit types |
| `maxHeaderLength` | header limit |
| `protectedBranches` | branches Claude may not commit on |
| `checkpointLines` | 0 disables the mid-work reminder |
| `commitOnTurnEnd` | `false` keeps the refusals but stops requiring a commit before the turn ends |

Switch off one guard without the rest via `disabledRules`: `git/protected-branch`,
`git/conventional-commit`, `git/stage-all`, `bash/force-push`, `bash/push-main`, `bash/no-verify`.

## Shared files

If a file Claude changed also holds your uncommitted work, it asks before committing it. That is the
one case where the gate defers to you rather than acting.

## Why it is enforced rather than requested

A commit message is the only durable explanation of *why* a change was made, and a clean history is
what makes `git bisect`, `git revert` and code review work. Conventional Commits also give you
changelog generation and semantic versioning for free. Asking nicely in `CLAUDE.md` works until the
session gets long; refusing the commit works every time.
