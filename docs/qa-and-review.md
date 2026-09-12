# QA & review loop

mast checks Claude's work while it works, so it hands back code that is analyzed, tested and reviewed —
not code you then have to send back.

```
write ──▶ analyzer on the whole file (high = refused) · block rules
edit  ──▶ warn rules · format · analyzer on the file · lint · checkpoint reminder
  │
turn ends ──▶ changelog + stop checks (once)
  │           analyzer over every file changed this turn ──┐
  │           tests covering those files ──────────────────┤ any failure → Claude fixes → checked again
  │           reviewer pass for 30+ lines ─────────────────┤ (up to qa.maxLoops rounds)
  │           commit checkpoint (when green) ──────────────┤
  └──────────────── all green ──────────────────────────────┴──▶ turn handed back
```

## Per write and per edit

Covered in [Hooks](hooks.md). The important asymmetry: a `Write` carries the whole file, so it can be
analyzed *before* it lands and refused; an `Edit` is a fragment, so it is analyzed immediately
afterwards and the findings come straight back.

## End of turn

### Once per turn

- **Changelog** — if files matching `changelog.requiredFor` changed and `changelog.file` didn't, Claude is asked to add a user-facing entry (or explain why none is needed).
- **Stop checks** — every enabled entry in `stopChecks` whose `when` matches an edited file (typecheck, full lint, `manage.py check`, missing migrations).

### Every round

- **The analyzer** over every Python file changed this turn. Findings in `analysis.gateOn` (default `high`) block the turn, with the check id and the fix. This is the gate that catches what an `Edit`-heavy session accumulated.
- **Tests** — `qa.relatedTests`. With `mapToTests` (the default) each changed module is mapped to `test_<module>.py` / `<module>_test.py` and only those run, so it takes seconds. **A changed module with no test file at all is reported as a gap** — that is usually the most valuable finding of the turn.
- **Review** — once the lines changed since the turn's first edit (committed or not, plus new files) reach `review.minChangedLines`, a passing review is required. The gate hands the reviewer that base commit.
  - no review yet → Claude must launch the reviewer agent
  - reviewer requested changes → Claude must fix them and run it again (or explain to you why a finding is wrong)
  - the review didn't end with a `REVIEW_RESULT` line → run it again

### Last: the commit checkpoint

When everything above passes, files Claude changed this turn that are still uncommitted must be
committed — Conventional Commits, explicit paths, no push. See [Commit discipline](commits.md).

Everything that failed is reported together. After `qa.maxLoops` (3) blocked rounds the turn is
released and logged as a `gate_giveup`.

## The reviewer agent

`.claude/agents/reviewer.md` is a read-only subagent (tools: Read, Grep, Glob, Bash). It reviews the
changes with fresh context, as a colleague would a PR:

1. runs `mast check` on the changed paths first, and treats those findings as already covered — it is there to find what no analyzer can
2. reads `CLAUDE.md` and the imported standards for the areas touched — a repeat of a recorded **grievance** is automatically `high`
3. checks, in order: correctness, security, tests, data and migrations, standards compliance (citing check or rule ids), design and reuse
4. verifies claims by reading code and running read-only commands, never edits
5. notes up to two **wins** worth reproducing

Severity: **high** must be fixed before merge (bug, security issue, broken contract, grievance repeat,
missing test for a fix, unreviewable migration); **medium** should be fixed in this change; **low** is
optional.

### The `REVIEW_RESULT` contract

The reviewer's output must end with exactly one line:

```
REVIEW_RESULT {"verdict":"changes_requested","findings":[{"severity":"high","category":"correctness","file":"src/shop/refunds.py","line":42,"rule":"core/testing","summary":"Refund is not idempotent on retry"}],"wins":["Clear separation of pricing rules from the transport layer"]}
```

The PostToolUse hook parses it; `review.blockOn` decides which severities count, overriding a lenient
verdict.

## The bug-hunter agent

`.claude/agents/bug-hunter.md` is for when you want defects found rather than a diff reviewed: before
a release, after a risky refactor, or when something fails in a way nobody can explain. It starts from
`mast check --format json`, then traces real execution paths and edge cases — empty, `None`, retries,
concurrency, partial failure, shared state, trusted-but-unvalidated data — and reports each defect with
the input that triggers it and the test that should accompany the fix. Run it with `/hunt`.

It is deliberately *not* in the end-of-turn gate: a deep hunt is worth doing on purpose, not on every
turn.
