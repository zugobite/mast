# Configuration

Everything per-repo lives in `.claude/standards.config.json`. mast creates it once, from the repo's own
tooling, and never overwrites it — only adding keys introduced by newer versions. Paths are
repo-relative; globs are gitignore-flavoured (a pattern without `/` matches at any depth).

```json
{
  "enabled": true,
  "commands": { "test": "uv run pytest", "lint": "uv run ruff check .", "typecheck": "uv run mypy .", "review": "mast check ." },
  "format": { "command": "uv run ruff format {file}", "globs": ["**/*.py"], "timeoutSec": 20 },
  "lint": { "command": "uv run ruff check --force-exclude {file}", "globs": ["**/*.py"], "timeoutSec": 30 },
  "analysis": { "enabled": true, "blockOn": ["high"], "gateOn": ["high"], "maxComplexity": 10 },
  "changelog": { "file": "CHANGELOG.md", "requiredFor": ["src/**", "tests/**"] },
  "stopChecks": [{ "name": "typecheck", "command": "uv run mypy .", "when": ["**/*.py"], "timeoutSec": 300, "enabled": false }],
  "qa": { "enabled": true, "maxLoops": 3, "codeGlobs": ["**/*.py"], "relatedTests": { "command": "uv run pytest -q {changed}", "mapToTests": true, "timeoutSec": 240 } },
  "review": { "enabled": true, "agent": "reviewer", "minChangedLines": 30, "blockOn": ["high", "medium"] },
  "commits": { "enabled": true, "checkpointLines": 150, "protectedBranches": ["main", "master"] },
  "rules": [],
  "disabledRules": [],
  "severityOverrides": {},
  "protectedPaths": [],
  "bashGuards": [],
  "learning": { "captureCorrections": true, "windowDays": 30 }
}
```

## Top level

| Key | Meaning |
| --- | --- |
| `enabled` | `false` switches every hook off in this repo |
| `commands` | the repo's canonical commands, shown to Claude in `CLAUDE.md`. Informational |
| `format` / `lint` | run on each edited file that matches `globs`. `{file}` is the quoted path. A failing lint comes back to Claude as something to fix |
| `analysis` | the analyzer — see [The analyzer](analyzer.md#settings) for every threshold |
| `changelog` | `file` plus `requiredFor` globs: changing matching files without touching the changelog is flagged once per turn |
| `stopChecks` | extra commands at the end of a turn (typecheck, a full lint, `manage.py check`). `when` limits them to relevant edits; `enabled: false` keeps one defined but off |
| `qa` | the test gate (below) |
| `review` | the reviewer gate (below) |
| `commits` | commit discipline — see [Commit discipline](commits.md) |
| `rules` | extra text rules, inline. Same shape as a pack's `rules.json` ([writing rules](rules.md)) |
| `disabledRules` | rule ids (and bash guard ids, and `git/protected-branch`, `git/conventional-commit`) to switch off |
| `severityOverrides` | `{"core/no-naive-now": "block"}`, or `"off"` |
| `protectedPaths` | extra `{globs, exclude, message}` entries agents may not write |
| `bashGuards` | extra `{id, pattern, message, flags}` shell commands to refuse |
| `learning` | `captureCorrections: false` stops praise/correction logging; `windowDays` sizes the session-start summary |

## `analysis`

Beyond the thresholds in [The analyzer](analyzer.md#settings):

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | `false` switches the analyzer out of the hooks entirely (`mast check` still works) |
| `blockOn` | `["high"]` | severities that refuse a write and come back as "must fix" after an edit |
| `gateOn` | `["high"]` | severities that block the end of a turn |

Both accept the lowest severity you want included: `["medium"]` also covers `high`.

## `qa`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | run the test gate at all |
| `maxLoops` | 3 | how many times the end-of-turn gate may block before releasing the turn |
| `codeGlobs` | `["**/*.py", "**/*.pyi"]` | which edited files count as code |
| `relatedTests.command` | detected | `{changed}` expands to the resolved test paths |
| `relatedTests.mapToTests` | `true` | map each changed module to `test_<module>.py` / `<module>_test.py` instead of passing the source file to pytest |
| `relatedTests.timeoutSec` | 240 | |

With `mapToTests`, a changed `src/shop/orders.py` runs `tests/**/test_orders.py`. If a changed module
has no test file at all, the gate says so and asks Claude to write one — that is usually the finding
you want most.

To run the full suite instead:

```json
"qa": { "relatedTests": { "command": "uv run pytest -q", "mapToTests": false, "timeoutSec": 600 } }
```

## `review`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | require an independent review for substantial changes |
| `agent` | `reviewer` | which subagent (a standard can ship its own) |
| `minChangedLines` | 30 | lines changed since the turn's first edit before a review is required |
| `blockOn` | `["high", "medium"]` | finding severities that count as "changes requested" |

## Recipes

**A legacy repo: hold new code to the standard, ignore the rest.**
```json
"analysis": { "blockOn": ["high"], "gateOn": ["high"], "requireTypeHints": false, "ignore": ["structure/large-module", "api/missing-docstring"] }
```

**A service where performance matters: gate on efficiency too.**
```json
"analysis": { "gateOn": ["medium"] }
```

**Django: skip migrations, check the project on every turn.**
```json
"analysis": { "excludeGlobs": ["**/migrations/**"] },
"stopChecks": [
  { "name": "django checks", "command": "uv run python manage.py check", "when": ["**/*.py"], "enabled": true },
  { "name": "missing migrations", "command": "uv run python manage.py makemigrations --check --dry-run", "when": ["**/models*.py"], "enabled": true }
]
```

**A data repo: notebooks are not production code.**
```json
"analysis": { "excludeGlobs": ["notebooks/**", "**/.ipynb_checkpoints/**"], "maxFunctionLines": 70 }
```

**Switch off the parts you don't want.** `lint: null`, `review.enabled: false`,
`qa.relatedTests: null`, `analysis.enabled: false`, `commits.enabled: false` — or `enabled: false`
for everything.

After editing the config, run `mast doctor` to confirm the commands exist and the rules still compile.
