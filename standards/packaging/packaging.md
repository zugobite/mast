---
description: Library and packaging standards — public API, versioning, dependencies, layout
globs: ["**/*.py", "pyproject.toml"]
alwaysApply: true
---

# Packaging and public API

## Layout

- `src/` layout with a `pyproject.toml` and a declarative build backend. It guarantees tests run against the installed package, not the working copy.
- The package's public API is what `__init__.py` exports and `__all__` lists. Everything else is private and may change; name it with a leading underscore or keep it in a `_internal` module.
- No import-time side effects: no I/O, no network, no logging configuration, no registry mutation when a module is imported.
- Ship `py.typed` so downstream type checkers use your annotations, and keep the package importable on the minimum Python version you claim.

## API design

- Keyword-only arguments (`*`) for anything optional, so adding a parameter is never a breaking change and calls stay readable.
- Accept wide types (`Iterable`, `Mapping`, `os.PathLike | str`), return precise ones. Never return `None` to mean failure — raise.
- Export your own exception hierarchy from one base class, and document which functions raise what.
- A library configures no logging and adds no handlers: `logging.getLogger(__name__)` and nothing else. It never calls `sys.exit()` — raise and let the caller decide.
- Deprecate before removing: `DeprecationWarning` with the version that will remove it, documented in the changelog, kept for at least one minor release.

## Versioning and dependencies

- Semantic versioning, single source of truth for the version, changelog entry in the same commit as the change.
- Depend on as little as possible, with honest lower bounds and upper bounds only where a real incompatibility exists. Never pin exact versions in a library — that is the application's job.
- Separate runtime, test and dev dependency groups; the lockfile belongs to applications, the constraint range to libraries.
- Test against every Python version you advertise, in CI, including the oldest.
