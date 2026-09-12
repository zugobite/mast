# Writing text rules

Rules complement the [analyzer](analyzer.md): one regex, one message, any file type. Use a rule when
what you want to catch is a *string* rather than a structure — a forbidden import, a setting in YAML,
a pattern in a Dockerfile — or when it must be caught in a partial edit before the file parses.

For anything semantic (a defect, a cost, an architectural rule), write a check instead: it sees the
real structure and produces far fewer false positives.

## Schema

```json
{
  "id": "acme/no-urllib",
  "severity": "warn",
  "globs": ["src/**/*.py"],
  "exclude": ["**/test_*.py"],
  "pattern": "^\\s*(import urllib|from urllib)",
  "flags": "i",
  "message": "Use the shared httpx client in acme.http — it carries retries and tracing."
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | unique, namespaced (`core/…`, `django/…`, `acme/…`), lowercase and dashes |
| `pattern` | yes | a **Python** regular expression, matched **per line** |
| `message` | yes | what Claude sees — say what to do instead, not just what is wrong |
| `severity` | no | `block` (deny the write) or `warn` (feedback after it). Default `warn` |
| `globs` | no | files the rule applies to. Empty or missing = every file |
| `exclude` | no | files to skip even when `globs` match |
| `flags` | no | `i` (ignore case), `m` (multiline), `s` (dotall), `x` (verbose) |

## How matching works

- Only the text **being added** is checked: `content` for `Write`, `new_string` for `Edit`, every
  `new_string` for `MultiEdit`, `new_source` for `NotebookEdit`. Existing code is never re-flagged.
- The text is split into lines and the pattern is searched in each. Reported line numbers are relative
  to the added text.
- Paths are repo-relative with `/` separators.

### Globs

| Syntax | Matches |
| --- | --- |
| `*` | anything except `/` |
| `**` | any number of directories |
| `?` | one character |
| `{a,b}` | either alternative |
| `name.py` | that file at any depth (no slash = any depth) |

## Where rules live

| Place | Scope | Managed? |
| --- | --- | --- |
| `standards/<pack>/rules.json` (mast) | every repo using that pack | yes — change here, then `mast sync` |
| `<standard>/packs/<pack>/rules.json` | every repo using your standard | yes |
| `<repo>/.claude/standards/local-rules.json` | that repo only | no — yours |
| `<repo>/.claude/standards.config.json` → `rules` | that repo only | no — yours |

Tune shared rules per repo with `disabledRules` and `severityOverrides` rather than editing the
managed file.

## Writing a good one

- **Precise over broad.** A rule that fires on correct code gets disabled, and then it protects nothing.
- **Anchor it.** `^\\s*import requests` rather than `requests`, so a comment or a docstring does not trip it.
- **Exclude tests** when the pattern is legitimate there.
- **Say what to do instead.** The message is an instruction to an agent, not a log line.
- **`block` only for things that must never land** — secrets, debuggers, destructive SQL. Everything else is a `warn`: it still comes back to Claude immediately.
- **Escape for JSON *and* regex**: a literal backslash is `\\\\` in the file.

## Checking them

```bash
mast standard check .     # validates every rule in a standard (use it in CI)
mast doctor <repo>        # confirms every active rule in a repo compiles
```

A rule with an invalid pattern is skipped rather than crashing a hook — `doctor` is how you find out.

## Promoting a rule from a mistake

`/grievance` can record a rule alongside the lesson, so the mistake is mechanically prevented next
time:

```bash
python3 .claude/standards/hooks/record_lesson.py <<'JSON'
{"kind":"grievance","lesson":"Never import urllib directly — use acme.http","scope":"global",
 "rule":{"id":"acme/no-urllib","severity":"warn","globs":["src/**/*.py"],"pattern":"^\\s*import urllib","message":"Use acme.http."}}
JSON
```

Then `mast learn`, `mast promote <id>`, `mast sync`.
