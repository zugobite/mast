# Troubleshooting

Start with:

```bash
mast doctor ~/code/my-api
```

## Hooks don't seem to run

- **Start a new session.** Claude Code loads hooks and agents when a session starts.
- Check `.claude/settings.json` has mast's five hooks (`doctor` reports `hooks registered (5/5)`). In the Claude Code CLI, `/hooks` shows what's loaded.
- `python3` must be on the `PATH` Claude Code uses. `doctor` reports the Python version it found (hooks need ≥ 3.10).
- `"enabled": false` in `.claude/standards.config.json` turns every hook off.

## "needs sync" in `mast status`

The core or the repo's standard changed since the last export. Run `mast sync`.

## Claude can't edit a file under `.claude/standards/`

Intended: packs, hooks, `rules.json` and `manifest.json` are managed. Change them in the core or your standard and run `mast sync`. Project-only changes go in `local-rules.json`, `local-lessons.md` or `local-wins.md`, which Claude may edit.

## Writes to `.env` are refused

Intended. Agents shouldn't write secrets; Claude will tell you which variable to set. `.env.example`, `.env.sample` and `.env.template` are allowed.

## A check is wrong about my code

Three ways out, in order of preference:

1. Fix the code.
2. Add `# mast: ignore[check/id]` with a reason on that line.
3. Turn the check off for the project in `analysis.ignore`.

If it is wrong in general, [open an issue](https://github.com/zugobite/mast/issues) — a false positive is a bug.

## A rule fires where it shouldn't

- Per repo: `"disabledRules": ["rule/id"]` or `"severityOverrides": { "rule/id": "off" }`.
- For everyone: fix the pattern or globs in its pack and `mast sync`.
- `mast learn` lists rules that never fire (candidates for pruning) and rules Claude ignores.

## Lint after every edit is slow or noisy

Multi-step changes can fail lint halfway (e.g. an import added before it's used). Set `"lint": null` and add a lint `stopChecks` entry instead — see [Configuration → recipes](configuration.md#recipes).

## Related tests are slow or wrong

Check `qa.relatedTests.command` runs by hand with a file:

```bash
uv run pytest -q tests/test_orders.py
```

Raise `timeoutSec`, point it at a faster command, or set `"relatedTests": null`.

## The review gate keeps asking for a review

- The reviewer must be launched as the `reviewer` agent (or whatever `review.agent` says).
- Its output must **end** with a single `REVIEW_RESULT {…}` line. If it doesn't, Claude is told the review wasn't recorded.
- High/medium findings count as "changes requested" even if the reviewer wrote `pass`.
- Editing code after a failed review requires a new review.

To see what the gate thinks, look at the session's state file in `.claude/standards/.state/` and the `review`/`gate_block` events in `.claude/standards/learning/events.jsonl`.

## A turn ended although checks were still failing

After `qa.maxLoops` rounds (default 3) the gate releases the turn and logs `gate_giveup`, so a broken check can never trap a session. `mast learn` flags these; look at the failing check.

## New config options don't appear in an existing repo

`mast sync` adds new top-level keys from newer mast versions but never changes keys you already have. Compare with [Configuration](configuration.md) and add sub-keys by hand.

## `mast learn --distill` fails

It needs the `claude` CLI on your `PATH`, logged in with available usage. The error includes the CLI's own message.

## `mast leakcheck` flags something that's fine

It's conservative. Rephrase the text, move it to a private repo, or — for a false positive in a private term — adjust that standard's `privateTerms`.

## Windows

Hook scripts are plain Python, but configured commands are run with POSIX shell quoting. Use WSL.

## Reset

```bash
rm ~/.mast/decisions.json        # forget promote/reject decisions
rm -r ~/.mast                    # forget everything (re-export repos afterwards)
mast remove ~/code/my-api --purge
```
