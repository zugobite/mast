---
description: How Claude should work in this repo — verify, analyze, test, review, commit
alwaysApply: true
---

# Working in this repo

## Before you change code

- Read the code you are about to change, and its tests, before editing. Match the patterns already there over patterns you prefer.
- Check the **Wins** sections for an approach that already earned approval, and the **Grievances** sections for a mistake already paid for. A repeat of a grievance is a high-severity review finding.
- Say what you are about to do in one line when the change is non-trivial, then do it.

## While you change code

- `mast check <paths>` on what you touched, before you say anything is done. The hooks run it too, but finding your own defects is faster than being told.
- A finding is a defect report. Fix it, or explain to the user why it is wrong here — never work around the check, disable it, or move the code to dodge a glob.
- `mast explain <check-id>` when a finding is unclear. The reasoning is there for a reason.
- Run the tests covering what you changed. Add the test that would have caught the bug you just fixed.
- Keep the diff to the task. Unrelated improvements are a separate commit, or a note to the user.

## Before you finish

- Tests green, analyzer clean, types checked, lint clean — in that order of importance.
- Commit your own files as Conventional Commits, staging explicit paths.
- Report honestly: what changed, what you verified, what you could not verify, what you left out. Never claim a check passed that you did not run.

## Never

- Never invent an API, a column, a setting or a library version. Read it or run it.
- Never leave a stub, a `TODO` placeholder or a `NotImplementedError` standing in for work the user asked for; finish it or say plainly that it is deferred and why.
- Never weaken a test, a type or a rule to make a check pass.
- Never touch secrets, `.env` files, production data or published history.
