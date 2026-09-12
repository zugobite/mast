# Hooks

Five hooks, registered in `.claude/settings.json`, each a standard-library Python script in
`.claude/standards/hooks/` with a vendored copy of the analyzer beside it. They read a JSON payload on
stdin and write JSON on stdout, exactly as Claude Code expects. mast's entries are identified by their
path, so `mast sync` can replace them without touching hooks you added.

All of them exit silently when `enabled` is `false` in the project config.

## SessionStart — orientation

Tells Claude, in one block:

- which standard and profile are active, and that the hooks enforce them
- that the analyzer runs on every Python file it touches, with the severity that blocks
- the rules it has violated most in this repo, and the analyzer's most frequent findings here
- which end-of-turn gates fired recently
- how many proven **wins** are recorded, and that their exemplars should be read first
- recent praise/correction counts, the QA/review setup, and the commit policy

It also prunes per-session state older than a week.

## UserPromptSubmit — the learning loop's ear

Classifies your message as praise or a correction (strong phrases anywhere; generic ones only at the
start, so "…and then stop." is not a correction), logs it, and nudges Claude to record a **win** or a
**grievance**. Skips slash commands and anything over 2000 characters. Disable with
`learning.captureCorrections: false`.

## PreToolUse — refusal

Matcher: `Write|Edit|MultiEdit|NotebookEdit|Bash`.

For a file write:

1. **protected paths** — `.claude/standards/{packs,hooks,rules.json,manifest.json}` are managed by mast; `.env` files are never written by an agent (`.env.example` is fine). Plus your `protectedPaths`.
2. **`block` text rules** — matched against *only the text being added*.
3. **the analyzer, for a full-file `Write`** — the content is parsed and analyzed before it lands; anything in `analysis.blockOn` (default `high`) is refused, with the finding and its fix.

An `Edit` cannot be analyzed this way — a fragment is not a module — so edits are caught by
PostToolUse instead.

For `Bash`, these are refused:

| Guard | What it stops |
| --- | --- |
| `bash/force-push` | `git push --force` (use `--force-with-lease`) |
| `bash/push-main` | pushing straight to `main`/`master` |
| `bash/no-verify` | `--no-verify` on commit or push |
| `bash/rm-root` | recursive delete of `/`, `~`, `$HOME` or the project root |
| `bash/print-env` | printing a `.env` file |
| `bash/chmod-world-write` | world-writable permissions |
| `git/stage-all` | `git add -A`, `git add .`, `git commit -a` |
| `git/protected-branch` | committing on `main`/`master` |
| `git/conventional-commit` | a commit header that is not a Conventional Commit |
| `python/publish-package` | `twine upload`, `poetry publish`, `flit publish`, `uv publish` |
| `python/destructive-db` | `manage.py flush`, `dropdb`, `DROP DATABASE`, `alembic downgrade base` |
| `python/pip-system` | `sudo pip install`, `--break-system-packages` |

Add your own with `bashGuards`; switch any of them off with `disabledRules`.

## PostToolUse — feedback

Matcher: `Write|Edit|MultiEdit|NotebookEdit|Task|Agent`.

For an edit, in order:

1. record the file in this turn's state (and the turn's base commit, on the first edit)
2. `warn` text rules against the added text
3. **format** the file (so line numbers that follow are the ones on disk)
4. **analyze** the file on disk — `analysis.blockOn` findings come back as "must fix" (a `block`
   decision, which feeds the reason straight back to Claude rather than undoing the edit); everything
   else as "also worth fixing"
5. **lint** the file; a failure comes back too
6. a **checkpoint reminder** once ~`commits.checkpointLines` of Claude's own work is uncommitted

For the reviewer subagent, it parses the final `REVIEW_RESULT` line, records the verdict for the Stop
gate, and logs every finding. A review without that line is recorded as `unknown` and asked for again.

## Stop — the gate

Runs when Claude tries to end a turn in which it edited files. Capped at `qa.maxLoops` (3) blocks per
turn, after which the turn is released and logged as `gate_giveup` — a broken gate can never trap a
session.

Once per turn:

- **changelog** — matching code changed without a changelog entry
- **stopChecks** — each enabled check whose `when` matches an edited file

Every round:

- **the analyzer** over every Python file changed this turn; `analysis.gateOn` findings block
- **tests** — the tests covering the changed modules (or the suite, if configured that way). No test file for a changed module is itself a finding
- **review** — once the lines changed since the turn's first edit reach `review.minChangedLines`, a passing review is required. Measuring from the turn's start means checkpoint commits never hide work from review

Last, when everything else is green:

- **the commit checkpoint** — Claude's own uncommitted files must be committed as Conventional Commits, staging explicit paths, never pushing

Everything that failed is reported together, so Claude can fix it in one pass.

## Events

Every hook appends JSON lines to `.claude/standards/learning/events.jsonl` (gitignored, rotated at
5 MB): `violation`, `analysis_finding`, `analysis_run`, `lint_fail`, `qa_run`, `review`,
`review_finding`, `review_win`, `gate_block`, `gate_giveup`, `turn`, `commit`, `praise`, `correction`.
`mast status` and `mast learn` read them. Nothing leaves the machine.

## Testing the hooks

`mast doctor` runs them for real. By hand:

```bash
echo '{"tool_name":"Write","tool_input":{"file_path":"a.py","content":"def f(x=[]):\n    return x\n"}}' \
  | CLAUDE_PROJECT_DIR=$PWD python3 .claude/standards/hooks/pre_tool.py
```
