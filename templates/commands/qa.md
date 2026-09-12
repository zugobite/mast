---
description: QA loop — analyzer, lint, types, tests on your changes, then the independent reviewer until it passes
argument-hint: [--full to run the whole test suite]
---

Run the QA loop on the current changes. $ARGUMENTS

1. **Scope** — changed files from `git status --porcelain` and `git diff HEAD --name-only`.
2. **Commands** — read `.claude/standards.config.json` (`commands`, `format`, `lint`, `analysis`, `qa.relatedTests`) for this repo's exact commands.
3. **Checks** — run in order; on a failure fix the cause, then re-run that step (max 3 attempts per step):
   1. format the changed files
   2. `mast check <changed paths>` — fix every high finding; fix mediums unless you can justify leaving them
   3. lint the changed files
   4. typecheck, if the repo has it
   5. the tests covering your changes (`qa.relatedTests`, or `test_<module>.py` for each changed module) — the whole suite if `--full` was passed
4. **Missing tests** — if you changed behaviour or fixed a bug and nothing covers it, write the test now; it must fail without your change.
5. **Independent review** — launch the `reviewer` agent (Agent tool, `subagent_type: "reviewer"`). If you already made checkpoint commits for this task, give it the base commit from before your first one so it reviews all of the work. Fix every high and medium finding, then re-run the reviewer until the verdict is `pass` (max 3 rounds). If you believe a finding is wrong, say so to the user with your reasoning instead of silently ignoring it.
6. **Checkpoint** — once everything is green, commit your changes as Conventional Commits (see `/checkpoint`). Never push.
7. **Report** — a checklist (step · result · notes), the review verdict and what you fixed, the commits made, and anything not verified. Never claim a step passed if you did not run it.
