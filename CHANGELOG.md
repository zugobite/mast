# Changelog

All notable changes to mast are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-09-12

### Fixed

- `bug/self-assignment` compares names rather than raw `ast.dump`, so `self.x = self.x` is reported (Store vs Load ctx used to hide it).

### Added

- `mast` CLI: `check` (text / JSON / SARIF, `--diff`, `--fail-on`), `explain`, `checks`, `list`, `export` (interactive picker, `--dry-run`, `--cursor`), `sync`, `status`, `remove`, `doctor`.
- AST analyzer: 81 checks across bugs, efficiency, structure, enterprise standards and security. Zero dependencies; vendored into every exported repo.
- Stock packs `core`, `web`, `django`, `fastapi`, `data`, `packaging` with 31 text rules, and profiles `base`, `web-service`, `django`, `fastapi`, `data`, `lib` with automatic detection from `pyproject.toml` / requirements / `manage.py`.
- Claude Code hooks: SessionStart context, praise/correction detection, pre-write analyzer + text-rule enforcement with shell guards and protected files, post-edit warn rules, format and lint, and an end-of-turn gate.
- QA & review loop: related tests on changed files, an independent `reviewer` subagent with a machine-readable verdict, bounded fix rounds, `/qa`, `/hunt` and `/ship`.
- Commit discipline: Conventional Commits on a branch, only Claude's files; `/checkpoint`; refused force-pushes, pushes to `main`, and `--no-verify`.
- Learning loop: wins and grievances from `/win`, `/grievance`, `/retro` and feedback detection; `mast learn` reports and recommendations, `--distill`, `promote` with exemplar files, `reject`.
- Layered standards: `standard.json`, `mast standard init | add | check | list | remove`, detect rules, config defaults, command and agent overlays, pack shadowing.
- `mast leakcheck` and `privateTerms` to keep private standards out of public repos.
- Per-user state in `~/.mast`.
