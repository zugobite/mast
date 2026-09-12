# Concepts

## The core and standards

mast has two layers:

- **The core** — this repo. The analyzer, the `mast` CLI, the hook scripts, the QA/review and learning loops, and a set of organisation-agnostic **stock packs**. It is public and knows nothing about any company.
- **Standards** — separate repos (usually private) that layer your own packs, profiles, detection rules and config defaults on top of the core. A company might have one standard; an agency one per client. See [Building a private standard](standards.md).

Every exported repo gets the core, plus at most one standard.

```
core (public) ── analyzer, engine, stock packs
   ▲
standard (private, optional) ── your packs, profiles, detect rules, config defaults, commands, agents
   │ mast export / sync
repo ── CLAUDE.md, .claude/…
```

## Checks vs rules

Two different mechanisms, deliberately:

| | **Checks** (the analyzer) | **Rules** (text) |
| --- | --- | --- |
| What | 81 `ast`-based checks built into mast | one regex per rule, in a pack's `rules.json` |
| Sees | the parsed structure of a Python module | one line of text, in any file type |
| Finds | semantic defects, cost, architecture | forbidden strings, config mistakes, non-Python files |
| Severity | `high` / `medium` / `low` | `block` / `warn` |
| Tuned by | `analysis` in the project config | `disabledRules`, `severityOverrides` |
| You add one by | writing a check in the mast repo ([how](analyzer.md#writing-a-check)) | adding JSON to a pack or `local-rules.json` ([how](rules.md)) |

Rules can catch things the analyzer never sees — a secret in a YAML file, `:latest` in a Dockerfile,
an unpinned requirement — and they apply to a partial edit before it is a parseable module.

## Packs

A **pack** is a folder of Markdown docs plus an optional `rules.json`:

```
packs/web/
├── web.md            # a standard Claude reads (imported into CLAUDE.md)
├── wins.md           # optional: promoted wins
├── grievances.md     # optional: promoted grievances
├── exemplars/        # optional: gold-standard files referenced by wins
└── rules.json        # optional: text rules the hooks enforce
```

Docs use Cursor-compatible frontmatter (`description`, `globs`, `alwaysApply`). mast imports every doc
of every pack in a profile into `CLAUDE.md`; the frontmatter matters for the `--cursor` mirror and as a
summary for humans. Keep docs tight — they load into every session.

A standard's pack **shadows** a core pack with the same name.

## Profiles

A **profile** says which packs a kind of repo gets, and can carry config defaults:

```json
{
  "description": "Core + HTTP + Django conventions",
  "packs": ["core", "web", "django"],
  "codePaths": ["src/**", "apps/**", "api/**", "config/**", "tests/**"],
  "config": {
    "analysis": { "excludeGlobs": ["**/migrations/**"] },
    "stopChecks": [{ "name": "django checks", "command": "{run}python manage.py check", "enabled": true }]
  }
}
```

`codePaths` decides which edits require a changelog entry (only paths whose top folder exists in the
repo are used). `config` holds defaults merged into a repo's config when it is first created; `{run}`
expands to the repo's runner prefix (`uv run`, `poetry run`, …).

## Hooks

mast registers five Claude Code hooks in `.claude/settings.json`. Each is a small Python script in
`.claude/standards/hooks/`, and they share a vendored copy of the analyzer.

| Event | What mast does |
| --- | --- |
| SessionStart | says what's active, which wins to reuse, and what Claude keeps getting wrong here |
| UserPromptSubmit | notices praise and corrections, logs them, nudges Claude to record lessons |
| PreToolUse | analyzes a full-file write and refuses high findings; blocks `block` rules, protected files, dangerous commands, bad commits |
| PostToolUse | analyzes the edited file, `warn` rules, format, lint, checkpoint reminder; records the reviewer's verdict |
| Stop | changelog and custom checks, the analyzer gate, related tests, the reviewer gate, the commit checkpoint |

See [Hooks](hooks.md).

## Resolution: which standard and profile does a repo get?

In order:

1. `--standard` / `--profile` passed to `mast export`
2. what the repo was last exported with (from the registry in `~/.mast`)
3. the first matching `detect` rule of any registered standard
4. the core's own detection (the table in [Getting started](getting-started.md#3-see-what-would-be-exported))

## Managed files vs your files

| Kind | Files | On `mast sync` |
| --- | --- | --- |
| **Managed** | `CLAUDE.md` block between `<!-- mast:start` and `<!-- mast:end -->`, `.claude/standards/{packs,hooks,rules.json,manifest.json}`, mast's commands and agents, mast's hooks in `settings.json` | rewritten from the source |
| **Yours** | everything else in `CLAUDE.md`, `.claude/standards.config.json`, `.claude/standards/local-wins.md`, `local-lessons.md`, `local-rules.json`, your own commands/agents/settings | never overwritten (new top-level config keys from newer mast versions are *added*) |
| **Local only** | `.claude/standards/learning/`, `.claude/standards/.state/` | gitignored, never committed |

The PreToolUse hook stops agents from editing managed files — change them at the source (core or
standard) and run `mast sync`.

**Precedence:** your content in `CLAUDE.md` outside the managed block, `local-wins.md` and
`local-lessons.md` win over shared standards when they conflict.

## Wins and grievances

mast's learning loop keeps two kinds of lessons:

- **Wins** — approaches you explicitly liked. Claude is told to reproduce them and to read their exemplar files first.
- **Grievances** — mistakes you corrected. Claude is told never to repeat them; a repeat is automatically high severity in review.

They start as local **candidates**, and become part of a standard only when you `mast promote` them.
See [Learning loop](learning-loop.md).

## State

| Where | What |
| --- | --- |
| `~/.mast/registry.json` | exported repos and registered standards (paths) |
| `~/.mast/decisions.json` | which candidates you promoted or rejected |
| `~/.mast/learning-report.md` | the latest `mast learn` report |
| `~/.mast/candidates.jsonl`, `distilled.json` | output of `mast learn --distill` |
| `<repo>/.claude/standards/learning/` | events and candidates for that repo |
| `<repo>/.claude/standards/.state/` | per-session gate state (auto-cleaned after 7 days) |
