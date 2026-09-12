# Building a private standard

A **standard** is a repo that layers your conventions on top of mast: your packs, your profiles, detection rules that pick them automatically, and config defaults. The core stays generic and public; the standard holds everything that's yours and can stay private.

A complete example lives in [`examples/acme-python`](../examples/acme-python).

## Create one

```bash
mast standard init ~/code/acme-python --name acme
```

This scaffolds, `git init`s and registers:

```
acme-python/
├── standard.json                 # identity, detection, defaults
├── profiles/acme.json            # core + acme-shared
├── packs/acme-shared/
│   ├── acme.md                   # your company context and conventions
│   ├── wins.md                   # promoted wins land here by default
│   ├── grievances.md             # promoted grievances land here by default
│   └── rules.json
├── commands/                     # extra slash commands for every repo
└── README.md
```

## `standard.json`

```jsonc
{
  "name": "acme",                          // lowercase, digits, dashes
  "description": "Acme Python engineering standards, layered on mast.",
  "core": ">=1.0.0",                       // minimum mast version
  "defaultPack": "acme-shared",            // where promoted global wins/grievances land
  "defaultProfile": "acme-base",           // used with --standard when no detect rule matches
  "privateTerms": ["AcmeCorp", "acme.internal"],   // must never appear in a public repo (mast leakcheck)
  "detect": [                              // first match wins
    { "profile": "acme-api", "name": "^acme-", "anyDependency": ["fastapi"] },
    { "profile": "acme-data", "name": "^acme-", "anyDependency": ["pandas", "polars"] },
    { "profile": "acme-base", "name": "^acme-" }
  ],
  "config": {                              // defaults for every repo's standards.config.json
    "severityOverrides": { "core/no-naive-now": "block" },
    "bashGuards": [{ "id": "acme/no-prod-deploy", "pattern": "deploy.*--prod", "message": "Production deploys go through CI." }]
  }
}
```

### Detect rules

Dependencies are read from `pyproject.toml` (PEP 621, Poetry, optional extras, dependency groups), `requirements*.txt`, `Pipfile` and `setup.cfg`.

| Field | Matches when |
| --- | --- |
| `profile` | (required) the profile to use |
| `name` | the repo folder name matches this regex (case-insensitive) |
| `anyDependency` | the repo declares at least one of these packages |
| `allDependencies` | the repo declares all of these |
| `noDependency` | the repo declares none of these |
| `hasFile` | at least one of these paths exists in the repo (`manage.py`, `dags/`, …) |

All given conditions must hold. Every registered standard is tried before the core's own detection; repos that match nothing stay on the plain core.

## Packs

Write docs as focused Markdown with frontmatter:

```markdown
---
description: Acme API conventions — layering, errors, auth
alwaysApply: true
---

# Acme APIs
…
```

- Reuse stock packs in your profiles (`core`, `web`, `django`, `fastapi`, `data`, `packaging`) and add your own on top.
- A pack with the same name as a core pack **replaces** it for your repos.
- Put checkable conventions in `rules.json` ([Writing rules](rules.md)); namespace ids (`acme/…`).
- Seed `grievances.md` with known recurring regressions — they are the most valuable lines you can write.

## Profiles

```json
{
  "description": "Acme FastAPI service",
  "packs": ["core", "web", "fastapi", "acme-shared", "acme-api"],
  "codePaths": ["src/**", "app/**", "tests/**"],
  "config": {
    "severityOverrides": { "web/no-wildcard-cors": "block" },
    "stopChecks": [
      { "name": "typecheck", "command": "{run}mypy .", "when": ["**/*.py"], "timeoutSec": 300 }
    ]
  }
}
```

A profile's `config` is merged after the standard's `config`. `{run}` expands to the repo's runner prefix (`uv run `, `poetry run `, `pipenv run `, or empty) so the same profile works across toolchains.

## Commands and agents

Files in `commands/` become slash commands in every repo using the standard; files in `agents/` become subagents. A file with the same name as a core one replaces it — e.g. ship `agents/reviewer.md` to review with your own checklist (keep the [`REVIEW_RESULT` contract](qa-and-review.md#the-review_result-contract)).

## Validate

```bash
mast standard check ~/code/acme-python
```

Checks names, core compatibility, that every profile's packs exist, detect rules, doc frontmatter, rule validity and unique rule ids. Add it to the standard's CI:

```yaml
- run: pip install -e ~/code/mast
- run: mast standard check .
```

## Roll it out

```bash
mast export ~/code/acme-billing      # auto-detected → acme/acme-api
mast sync                            # after every change to the standard
```

For your team: each developer installs mast, clones the standard, and registers it once:

```bash
git clone https://github.com/acme/acme-python.git ~/code/acme-python
mast standard add ~/code/acme-python
```

The exported files are committed in each repo, so hooks and standards work for everyone — mast itself is only needed to export, sync and learn.

## Keep it private

If the standard contains company information, keep the repo private and list the names that identify you in `privateTerms`. `mast leakcheck` in any public repo — including your fork of the core — fails when one of them appears. Promote company lessons into the standard (the default), not the core.
