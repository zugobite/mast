# Getting started

## 1. Install

mast is installed from a clone, because it ships its standards, hooks and templates as files you are
meant to read and edit:

```bash
git clone https://github.com/zugobite/mast ~/code/mast
pip install -e ~/code/mast       # or: pipx install -e ~/code/mast
mast --version
```

Requirements: Python ≥ 3.10 and git. No third-party packages are installed.

If you keep the clone somewhere unusual, set `MAST_HOME` to it. `mast check` works from any install;
`export`, `sync` and the learning loop need the data directories.

## 2. Hunt for bugs in a repo you already have

Nothing to set up — point it at code:

```bash
cd ~/code/my-api
mast check src                  # or just: mast check
mast check --diff main          # only what this branch changed
mast explain perf/n-plus-one-query
```

Every finding has a severity (`high`, `medium`, `low`), a check id and a one-line fix. `mast check`
exits 1 when anything reaches `--fail-on` (default `high`), so it works in CI and in pre-commit as-is.

→ [The analyzer](analyzer.md) · [all 81 checks](checks.md)

## 3. See what would be exported

```bash
mast list                       # profiles, packs, and already-exported projects
mast export ~/code/my-api --dry-run
```

mast reads `pyproject.toml`, `requirements*.txt`, `Pipfile` and `setup.cfg` to pick a profile:

| Profile | Packs | Picked when the repo has |
| --- | --- | --- |
| `django` | core · web · django | Django, or a `manage.py` |
| `fastapi` | core · web · fastapi | FastAPI |
| `web-service` | core · web | Flask, Starlette, aiohttp, Litestar, Sanic, Tornado… |
| `data` | core · data | pandas, numpy, polars, scikit-learn, Spark, Airflow, dbt… |
| `lib` | core · packaging | a build backend and no app framework |
| `base` | core | anything else |

Override with `--profile <name>`.

## 4. Export

```bash
mast export ~/code/my-api
```

That writes, into the repo:

- `CLAUDE.md` — a managed block that `@import`s the standards (your own content is kept)
- `.claude/standards/packs/**` — the standards Claude reads
- `.claude/standards/rules.json` — text rules the hooks enforce
- `.claude/standards/hooks/**` — the five hooks **and a vendored copy of the analyzer**
- `.claude/standards.config.json` — your project's config, detected from its own tooling
- `.claude/agents/{reviewer,bug-hunter}.md`, `.claude/commands/*.md`
- `.claude/settings.json` — mast's hook entries merged into whatever was there

Then verify it:

```bash
mast doctor ~/code/my-api
```

`doctor` checks the wiring and actually runs the hooks: it confirms a bad write is refused, a clean
write is allowed, a force-push is blocked, and the vendored analyzer loads. Fix anything it flags
before relying on the loop.

## 5. Commit it

```bash
cd ~/code/my-api
git add CLAUDE.md .claude .gitignore
git commit -m "build: add mast standards and hooks"
```

Committing is what makes the setup work for your teammates — they need nothing but Python.

## 6. Work as usual

Start a new Claude Code session in the repo. You should see a line like:

```
[mast] Profile "fastapi" (core v1.0.0) is active. … The analyzer (81 checks) runs on every Python
file you touch and again before the turn ends; high-severity findings block.
```

From then on:

- a write with a high-severity defect is **refused** before it lands, with the reason and the fix
- every edit is analyzed, formatted and linted, and problems come straight back to Claude
- at the end of a turn the analyzer runs over everything changed, the tests covering those files run,
  and substantial changes need a pass from the `reviewer` agent
- Claude commits its own work in small Conventional Commits, on a branch, and never pushes

Ask for `/qa` before you review anything yourself, `/hunt` when something is mysterious, `/ship`
when it is ready for a PR.

## 7. Keep it in sync

```bash
mast status          # which repos drifted, and what the hooks have been seeing
mast sync            # re-export every registered repo
```

Run `mast sync` after you change a pack, add a rule, or upgrade mast.

## 8. Feed the loop

When Claude does something you like, or gets something wrong, say so — then:

```bash
mast learn                    # the report: wins, grievances, findings, recommendations
mast promote <id>             # make it a standard
mast sync                     # every repo gets it
```

→ [Learning loop](learning-loop.md)

## Optional: a private company layer

```bash
mast standard init ~/code/acme-python --name acme
```

→ [Building a private standard](standards.md)
