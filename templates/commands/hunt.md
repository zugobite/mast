---
description: Deep bug hunt over the current changes or a named area, with reproductions
argument-hint: [path, module or "diff" — defaults to the current changes]
---

Hunt for real defects: $ARGUMENTS

1. **Scope** — what the argument names; with no argument, the current changes (`git diff HEAD --name-only` plus untracked files).
2. **Machine pass** — `mast check <scope> --explain` (or `mast check --diff main` for a branch). Fix every high finding yourself before hunting further; they are defects, not suggestions.
3. **Human pass** — launch the `bug-hunter` agent (Agent tool, `subagent_type: "bug-hunter"`) on the same scope, and tell it what the change was meant to do. It traces execution paths and edge cases the analyzer cannot see.
4. **Triage** — for each reported defect decide: fix now, fix in a follow-up, or not a bug (say why). Confirm the ones you fix with a test that fails without the fix.
5. **Report** — what was examined, what was found and fixed, what was deliberately left, and the highest-risk area still unverified.
