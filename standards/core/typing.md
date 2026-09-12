---
description: Type hints as an enforced contract — annotations, strictness, boundaries
globs: ["**/*.py", "**/*.pyi"]
alwaysApply: true
---

# Typing

Types are the only part of the contract a machine can check. In an enterprise codebase they are
the difference between a refactor that is verified and one that is hoped for.

## Rules

- Every public function is fully annotated: all parameters and the return type. `-> None` for procedures.
- Run a type checker in CI (`mypy --strict` or `pyright` in strict mode) and keep it green. A failing type check is a failing build.
- `Any` switches checking off — use a concrete type, a `Protocol` for duck typing, a `TypeVar` for generics, or `object` when you genuinely accept anything. If `Any` is unavoidable at an untyped boundary, normalise into a typed shape immediately.
- No bare `# type: ignore`. Narrow it (`# type: ignore[arg-type]`) and say why on the same line.
- Use modern syntax: `list[str]`, `dict[str, int]`, `X | None`. Not `List`, `Dict`, `Optional[X]`.
- `Protocol` over inheritance for pluggable behaviour — it keeps implementations independent of the interface.
- `Literal` and `Enum` for closed sets; `NewType` for ids that must not be mixed up (`UserId = NewType("UserId", int)`).
- `TypedDict` for the shape of external JSON; a dataclass or model for anything you own.
- Annotate what a function *accepts* as widely as is honest (`Iterable[str]`, `Mapping[str, int]`) and what it *returns* as precisely as possible (`list[str]`, `dict[str, int]`).

## Boundaries

- Parse, don't validate-and-hope: turn untrusted input into a typed object at the edge (pydantic, a dataclass with a classmethod, `attrs` validators) and pass that object inward.
- Keep `.pyi` stubs only for genuinely untyped third-party code, and pin the version they describe.
- `from __future__ import annotations` at the top of modules with forward references, so annotations stay cheap and order-independent.
