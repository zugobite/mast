# CLI reference

```
mast <command> [options]
mast --version
mast <command> --help
```

## Analyze

### `mast check [paths…]`

Run the analyzer. Paths default to the current directory; directories are searched recursively for
`.py` files, minus `analysis.excludeGlobs`.

| Option | Meaning |
| --- | --- |
| `--format text\|json\|sarif` | output format (default `text`) |
| `--fail-on high\|medium\|low\|none` | exit 1 when a finding reaches this severity (default `high`) |
| `--select ID` | only these checks, categories or prefixes — repeatable (`--select bugs --select sec/`) |
| `--ignore ID` | skip these checks, categories or prefixes — repeatable |
| `--diff REF` | only report findings on lines changed since `REF` (plus whole untracked files) |
| `--explain` | print the reasoning and the fix with each finding |
| `--no-source` | omit the offending source line |
| `--stats` | print the check count and where settings came from |
| `--config FILE` | read analyzer settings from this JSON file instead of discovering them |

### `mast explain <check…>`

Why a check exists and how to fix what it finds. Accepts a check id, a category (`security`) or a
prefix (`perf/`).

### `mast checks`

List every check. `--category`, `--severity`, `--format text|json`.

## Export

### `mast list`

The core and every registered standard: profiles, packs with doc and rule counts, and every exported
project.

### `mast export [dir…]`

Export standards, rules, hooks and the analyzer into repos. With no directory it shows an interactive
picker over `MAST_ROOTS`.

| Option | Meaning |
| --- | --- |
| `--standard NAME\|PATH` | layer a standard on the core (otherwise auto-detected from its `standard.json`) |
| `--profile NAME` | force a profile instead of detecting one |
| `--cursor` | also mirror the standards into `.cursor/rules/mast-*.mdc` |
| `--dry-run` | print what would happen, write nothing |
| `-y`, `--yes` | skip the confirmation |

### `mast sync [--dry-run]`

Re-export every registered project with whatever it was exported with. Run it after changing a pack,
a rule or a check, or after upgrading mast.

### `mast status`

One line per project: standard/profile, whether it is in sync, and what the hooks recorded in the last
30 days (violations, praise, corrections, gate blocks, analyzer findings, pending lessons).

### `mast remove <dir> [--purge] [-y]`

Remove mast's managed files and hook entries. `--purge` also deletes the project config and its local
learning data.

### `mast doctor [dir]`

Validate a project and **self-test its hooks**: Python version, export freshness, hook registration,
config and rule validity, the vendored analyzer, formatter/linter on `PATH`, and live runs proving a
bad write is refused, a clean write is allowed, and a force-push is blocked. Exit 1 on any problem.

## Standards

| Command | Meaning |
| --- | --- |
| `mast standard init <dir> [--name n] [--description d]` | scaffold and register a new standard repo |
| `mast standard add <dir>` | register an existing standard |
| `mast standard remove <name>` | unregister (refused while projects still use it) |
| `mast standard check [dir]` | validate a standard — use it in the standard's CI |
| `mast standard list` | registered standards and how many projects use each |

## Publish

### `mast leakcheck [dir] [--term TEXT]`

Scan everything git would publish (tracked + untracked, not ignored) for private keys, cloud
credentials, credential-shaped assignments, home paths, email addresses, and every registered
standard's `privateTerms`. Defaults to the mast repo itself. Exit 1 on any hit; secrets are redacted
in the output.

## Learn

### `mast learn [--days 30] [--distill]`

Aggregate every exported project's events: rule violations, analyzer findings, gate blocks, review
verdicts, test results, praise and corrections. Prints recommendations and pending lessons, and writes
the full report to `~/.mast/learning-report.md`.

`--distill` first turns recent praise and corrections into lesson candidates using your own `claude`
CLI (the only thing in mast that talks to a model).

### `mast promote <id> [--scope global|project] [--pack NAME] [--sync]`

Turn a candidate into a standard. `global` writes into the project's standard (its `defaultPack`) or
the core with `--pack core`; `project` appends to the repo's `local-wins.md` / `local-lessons.md`.
Wins carry their exemplar files into the pack.

### `mast reject <id>`

Dismiss a candidate so it stops appearing.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAST_HOME` | the clone containing the package | where mast's `standards/`, `profiles/`, `hooks/`, `templates/` live |
| `MAST_STANDARDS_DIR` | `$MAST_HOME/standards` | override just the stock packs |
| `MAST_STATE_DIR` | `~/.mast` | registry, decisions, reports, central candidates |
| `MAST_ROOTS` | `~/Developer` | colon-separated directories the interactive picker scans |
| `MAST_NO_LOG` | — | set by `mast doctor`; stops hooks writing learning events |
| `NO_COLOR` | — | disable colour |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success (and, for `check`, nothing at or above `--fail-on`) |
| 1 | findings at or above the threshold, a failed validation, or a usage error |
| 130 | interrupted |
