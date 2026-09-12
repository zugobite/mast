---
description: Python coding standards — structure, naming, idioms and the things that cause real bugs
globs: ["**/*.py", "**/*.pyi"]
alwaysApply: true
---

# Python coding standards

The analyzer (`mast check`) enforces the mechanical half of this document. Treat a finding as a
defect report, not a style note — `mast explain <check-id>` says why each one matters.

## Structure

- One responsibility per function and module. Keep I/O (HTTP, DB, filesystem) at the edges and pure logic in the middle, so the logic is testable without mocks.
- Guard and return early. Deep nesting is where wrong-branch bugs live; past four levels, extract a function.
- Functions stay under ~50 lines and ~10 branches. When a function needs a comment to announce its next "section", that section is a function.
- No magic numbers or strings for limits, timeouts or keys — name them as module constants in one place.
- Delete dead code instead of commenting it out. Git remembers.
- `__init__.py` re-exports the package's public API and nothing else; no logic, no side effects at import time.

## Naming

- Names say *what*, not *how*: `resolve_invoice_total`, not `process_data2`.
- `snake_case` for functions, variables and modules; `PascalCase` for classes; `UPPER_SNAKE` for module constants.
- A leading underscore marks everything that is not public API — treat the rest as a contract you cannot break without a version bump.
- Booleans read as assertions: `is_active`, `has_access`, `can_retry`.
- Unused parameters are prefixed with `_`.

## Idioms that prevent bugs

- **Never use a mutable default argument.** `def f(items=[])` shares one list across every call. Default to `None` and build inside.
- Mutable state that lives longer than a request is shared state: no module-level lists or dicts that are mutated at runtime, and no mutable class attributes — set them in `__init__` or use `field(default_factory=...)`.
- Bind loop variables explicitly when a closure outlives the iteration (`lambda x=item: ...`), otherwise every closure sees the last value.
- Never mutate a collection while iterating it; iterate over a copy or build a new one.
- `is` is for `None`, `True`, `False` and sentinels. Use `==` for values.
- Use `with` for anything that must be released — files, locks, connections, transactions.
- Prefer the comprehension or the stdlib function over the hand-rolled loop: `enumerate`, `zip`, `itertools`, `collections.Counter`, `pathlib` over `os.path`.
- Datetimes are timezone-aware end to end: `datetime.now(timezone.utc)`, never `utcnow()`.

## Data shapes

- Model data with `@dataclass(frozen=True, slots=True)`, `NamedTuple` or a pydantic model — not with dicts passed between layers. A dict is an untyped contract nobody can refactor.
- Validate at the boundary, then trust the typed object inward.
- Prefer immutability: return new values instead of mutating arguments. A function that mutates its input says so in its name.

## Comments and docstrings

- Comments explain *why*; the code already says what. A comment that restates the code gets deleted.
- Every public module, class and function gets a docstring covering what it does, what it raises, and anything surprising (units, side effects, ordering guarantees).
- Record non-obvious contracts next to the code that enforces them, not in a wiki.
