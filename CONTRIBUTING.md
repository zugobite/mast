# Contributing to mast

Thanks for helping! mast is small on purpose — please keep it that way.

## Setup

```bash
git clone https://github.com/zugobite/mast.git
cd mast
python3 -m venv .venv && source .venv/bin/activate
pip install -e .          # puts your working copy's `mast` on PATH
python -m unittest discover -s tests
```

No dependencies: mast and its hooks use the standard library only (Python ≥ 3.10).

## Layout

```
src/mast/             CLI, export, learn, leakcheck, standard, analyzer
src/mast/analysis/    81 AST checks (bugs, efficiency, structure, standards, security)
hooks/                the Claude Code hooks copied into every repo
standards/<pack>/     stock packs (docs + rules.json)
profiles/*.json       stock profiles
templates/            commands and agents copied into every repo
examples/             a complete example standard
tests/                the test suite (unittest)
scripts/generate_docs.py   regenerates docs/checks.md from the registry
docs/                 user documentation
```

## Principles

- **Hooks must never break a session.** They read JSON from stdin, tolerate bad input, never throw, and stay fast. Logging failures are swallowed.
- **User-owned files are sacred.** Only managed files are rewritten; configs and `local-*` files are created once.
- **Stock packs are organisation-agnostic.** No company names, internal helpers or private contracts — those belong in a [private standard](docs/standards.md).
- **Rules need a doc.** A rule enforces; the pack's doc explains why and what to do instead.
- **A finding must be a defect or a real cost.** If a careful reviewer would say "that's fine", it is a false positive. Precision over recall.
- **Docs change with behaviour.** If you change what a command, hook or config key does, update `docs/` in the same PR.

## Before opening a PR

```bash
python -m unittest discover -s tests
python scripts/generate_docs.py --check
mast leakcheck .
mast standard check examples/acme-python
```

- Add or extend a test for every behaviour change (hooks are tested by piping JSON into them — see `tests/test_hooks.py`).
- Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, …).
- Add a line to `CHANGELOG.md` under `## [Unreleased]`.

## Adding a check

1. Add it to `src/mast/analysis/{bugs,efficiency,structure,standards,security}.py` with `@check(...)` — see [Writing a check](docs/analyzer.md#writing-a-check).
2. Add both-ways tests (the defect is caught, the correct version is silent) in the matching `tests/test_*.py`.
3. Run `python scripts/generate_docs.py` so `docs/checks.md` matches the registry.

## Adding a stock rule

1. Add it to the pack's `rules.json` with a namespaced id and an actionable message.
2. Explain the standard in the pack's doc.
3. Add positive and negative cases to the tests.
4. List it in [docs/packs.md](docs/packs.md).

## Reporting bugs

Open an issue with your OS, Python version, `mast doctor` output and the smallest repo that reproduces it. For security issues, see [SECURITY.md](SECURITY.md).
