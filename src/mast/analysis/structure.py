"""Structure: size, complexity, coupling and duplication — what makes code hard to change."""

from __future__ import annotations

import ast
from typing import Iterator, Sequence

from .context import (
    FUNCTION_TYPES,
    ModuleContext,
    body_lines,
    complexity,
    nesting_depth,
)
from .model import Finding, check


def _label(ctx: ModuleContext, func: ast.AST) -> str:
    klass = ctx.enclosing_class(func)
    name = getattr(func, "name", "<lambda>")
    return f"{klass.name}.{name}" if klass else name


@check(
    id="structure/long-function",
    category="structure",
    severity="medium",
    title="Function is too long",
    why=(
        "A function longer than a screen cannot be held in your head at once, so reviewers skim it and "
        "changes are made by local patching. Length is also the strongest predictor of defect density."
    ),
    fix="Extract the cohesive steps into named helpers — usually the blocks you would otherwise write a comment above.",
)
def long_function(ctx: ModuleContext) -> Iterator[Finding]:
    limit = ctx.settings.max_function_lines
    for func in ctx.functions():
        if ctx.enclosing_function(func) is not None:
            continue
        lines = body_lines(func)
        if lines > limit:
            yield ctx.report(func, f"`{_label(ctx, func)}()` is {lines} lines (limit {limit}) — extract the steps into named helpers", symbol=_label(ctx, func))


@check(
    id="structure/high-complexity",
    category="structure",
    severity="medium",
    title="Function has too many branches",
    why=(
        "Cyclomatic complexity counts the independent paths through a function. Past ~10, the paths cannot "
        "all be covered by tests or held in mind, and each new condition multiplies the untested combinations."
    ),
    fix="Return early to flatten guards, move branch bodies into functions, or replace the branching with a lookup table or polymorphism.",
)
def high_complexity(ctx: ModuleContext) -> Iterator[Finding]:
    limit = ctx.settings.max_complexity
    for func in ctx.functions():
        score = complexity(func)
        if score > limit:
            yield ctx.report(func, f"`{_label(ctx, func)}()` has a cyclomatic complexity of {score} (limit {limit}) — split the branching up", symbol=_label(ctx, func))


@check(
    id="structure/deep-nesting",
    category="structure",
    severity="medium",
    title="Deeply nested blocks",
    why="Every level of nesting is a condition the reader must keep on the stack. Deep nesting is where off-by-one and wrong-branch bugs hide.",
    fix="Invert conditions and return early, extract the inner block into a function, or use `continue` to skip cases in loops.",
)
def deep_nesting(ctx: ModuleContext) -> Iterator[Finding]:
    limit = ctx.settings.max_nesting
    for func in ctx.functions():
        depth, culprit = nesting_depth(func)
        if depth > limit and culprit is not None:
            yield ctx.report(culprit, f"nested {depth} levels deep inside `{_label(ctx, func)}()` (limit {limit}) — flatten with early returns or extract a helper", symbol=_label(ctx, func))


@check(
    id="structure/too-many-arguments",
    category="structure",
    severity="medium",
    title="Function takes too many arguments",
    why=(
        "A long parameter list means the function has many reasons to change and callers must get an exact "
        "order right. Positional mistakes between same-typed arguments are silent."
    ),
    fix="Group related parameters into a dataclass or TypedDict, or split the function. Make the rest keyword-only with `*`.",
)
def too_many_arguments(ctx: ModuleContext) -> Iterator[Finding]:
    limit = ctx.settings.max_arguments
    for func in ctx.functions():
        args = func.args
        names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
        if names and names[0] in {"self", "cls"}:
            names = names[1:]
        if len(names) > limit:
            yield ctx.report(func, f"`{_label(ctx, func)}()` takes {len(names)} arguments (limit {limit}) — group them into a dataclass or split the function", symbol=_label(ctx, func))


@check(
    id="structure/too-many-locals",
    category="structure",
    severity="low",
    title="Function juggles too many local variables",
    why="A large number of locals usually means several concerns share one scope, so a reader cannot tell which variables belong to which step.",
    fix="Extract each concern into its own function so its variables live and die there.",
)
def too_many_locals(ctx: ModuleContext) -> Iterator[Finding]:
    limit = ctx.settings.max_locals
    for func in ctx.functions():
        if ctx.enclosing_function(func) is not None:
            continue
        names = {
            n.id
            for n in ast.walk(func)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and ctx.enclosing_function(n) is func
        }
        if len(names) > limit:
            yield ctx.report(func, f"`{_label(ctx, func)}()` holds {len(names)} local variables (limit {limit}) — split the concerns apart", symbol=_label(ctx, func))


@check(
    id="structure/large-class",
    category="structure",
    severity="medium",
    title="Class has too many methods",
    why=(
        "A class with dozens of methods is several classes sharing a namespace: unrelated features change the "
        "same file, tests need the whole object built, and nothing can be reused separately."
    ),
    fix="Split by responsibility — the methods that touch the same attributes belong together.",
)
def large_class(ctx: ModuleContext) -> Iterator[Finding]:
    method_limit = ctx.settings.max_class_methods
    line_limit = ctx.settings.max_class_lines
    for klass in ctx.nodes(ast.ClassDef):
        methods = [n for n in klass.body if isinstance(n, FUNCTION_TYPES)]
        lines = body_lines(klass)
        if len(methods) > method_limit:
            yield ctx.report(klass, f"`{klass.name}` has {len(methods)} methods (limit {method_limit}) — split it by responsibility", symbol=klass.name)
        elif lines > line_limit:
            yield ctx.report(klass, f"`{klass.name}` is {lines} lines (limit {line_limit}) — split it by responsibility", symbol=klass.name)


@check(
    id="structure/large-module",
    category="structure",
    severity="low",
    title="Module is too long",
    why="A very long module is a merge-conflict magnet and hides its own structure: nobody can see what belongs with what.",
    fix="Split it into a package of focused modules along the seams the imports already suggest.",
)
def large_module(ctx: ModuleContext) -> Iterator[Finding]:
    limit = ctx.settings.max_module_lines
    count = len(ctx.lines)
    if count > limit and not ctx.is_test:
        yield ctx.report(ctx.tree.body[0] if ctx.tree.body else ast.Module(), f"{ctx.rel} is {count} lines (limit {limit}) — split it into focused modules")


@check(
    id="structure/wildcard-import",
    category="structure",
    severity="medium",
    title="Wildcard import",
    why=(
        "`from x import *` makes it impossible to tell where a name came from, lets an upstream release "
        "shadow local names silently, and defeats every static analysis tool including your IDE."
    ),
    fix="Import the names you use explicitly.",
)
def wildcard_import(ctx: ModuleContext) -> Iterator[Finding]:
    for node in ctx.nodes(ast.ImportFrom):
        if any(alias.name == "*" for alias in node.names):
            yield ctx.report(node, f"`from {node.module or '.'} import *` hides where names come from — import them explicitly")


@check(
    id="structure/duplicate-block",
    category="structure",
    severity="medium",
    title="Duplicated function body",
    why=(
        "Two functions with the same shape mean a fix has to be found twice. In practice one copy gets "
        "updated and the other keeps the bug, which is how behaviour silently diverges between call sites."
    ),
    fix="Extract the shared body into one function and parameterise what differs.",
    project_wide=True,
)
def duplicate_block(modules: Sequence[ModuleContext]) -> Iterator[Finding]:
    seen: dict[str, tuple[ModuleContext, ast.AST, str]] = {}
    for ctx in modules:
        if ctx.is_test:
            continue
        minimum = ctx.settings.min_duplicate_statements
        for func in ctx.functions():
            statements = [s for s in ast.walk(func) if isinstance(s, ast.stmt)]
            if len(statements) - 1 < minimum:
                continue
            key = _normalised(func)
            previous = seen.get(key)
            if previous is None:
                seen[key] = (ctx, func, _label(ctx, func))
                continue
            other_ctx, other_func, other_label = previous
            ctx._current = "structure/duplicate-block"
            where = f"{other_ctx.rel}:{other_func.lineno}" if other_ctx is not ctx else f"line {other_func.lineno}"
            yield ctx.report(
                func,
                f"`{_label(ctx, func)}()` duplicates `{other_label}()` ({where}) — extract the shared logic",
                symbol=_label(ctx, func),
            )


def _normalised(func: ast.AST) -> str:
    """AST shape with locals alpha-renamed, so copies with different variable names still match."""
    mapping: dict[str, str] = {}
    parts: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, FUNCTION_TYPES) and node is func:
            parts.append("def")
            continue
        if isinstance(node, ast.Name):
            parts.append(mapping.setdefault(node.id, f"v{len(mapping)}"))
        elif isinstance(node, ast.Attribute):
            parts.append(f".{node.attr}")
        elif isinstance(node, ast.Constant):
            parts.append(repr(node.value))
        elif isinstance(node, ast.arg):
            parts.append(mapping.setdefault(node.arg, f"v{len(mapping)}"))
        else:
            parts.append(type(node).__name__)
    return "|".join(parts)


@check(
    id="structure/import-cycle",
    category="structure",
    severity="high",
    title="Circular import between modules",
    why=(
        "Modules that import each other can only be loaded in one order. The result is an `ImportError` or a "
        "half-initialised module that depends on which entry point ran first — and it breaks in production "
        "long before it breaks in a test."
    ),
    fix="Extract the shared types into a third module, import inside the function that needs it, or invert the dependency with a protocol.",
    project_wide=True,
)
def import_cycle(modules: Sequence[ModuleContext]) -> Iterator[Finding]:
    by_name: dict[str, ModuleContext] = {}
    for ctx in modules:
        by_name[_module_name(ctx.rel)] = ctx
    edges: dict[str, dict[str, ast.AST]] = {}
    for name, ctx in by_name.items():
        package = name.rsplit(".", 1)[0] if "." in name else ""
        targets: dict[str, ast.AST] = {}
        for node in ctx.nodes(ast.Import, ast.ImportFrom):
            if ctx.enclosing_function(node) is not None:
                continue  # a deferred import is the documented way out of a cycle
            for candidate in _import_targets(node, package):
                resolved = _resolve(candidate, by_name)
                if resolved and resolved != name:
                    targets.setdefault(resolved, node)
        edges[name] = targets

    reported: set[frozenset[str]] = set()
    for start in edges:
        stack = [(start, [start])]
        while stack:
            node, path = stack.pop()
            for neighbour in edges.get(node, {}):
                if neighbour == start and len(path) > 1:
                    cycle = frozenset(path)
                    if cycle in reported:
                        continue
                    reported.add(cycle)
                    ctx = by_name[start]
                    ctx._current = "structure/import-cycle"
                    chain = " → ".join(path + [start])
                    yield ctx.report(edges[start][path[1]], f"circular import: {chain}")
                elif neighbour not in path and len(path) < 6:
                    stack.append((neighbour, path + [neighbour]))


def _module_name(rel: str) -> str:
    name = rel[:-3] if rel.endswith(".py") else rel
    name = name.replace("/", ".")
    return name[: -len(".__init__")] if name.endswith(".__init__") else name


def _import_targets(node: ast.AST, package: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    level = getattr(node, "level", 0) or 0
    base = package
    for _ in range(max(0, level - 1)):
        base = base.rsplit(".", 1)[0] if "." in base else ""
    module = node.module or ""
    head = ".".join(p for p in (base if level else "", module) if p) if level else module
    return [head] + [f"{head}.{alias.name}" for alias in node.names if alias.name != "*"]


def _resolve(candidate: str, by_name: dict[str, ModuleContext]) -> str | None:
    """Match an import against the analyzed modules, allowing for a `src/` prefix."""
    if candidate in by_name:
        return candidate
    for name in by_name:
        if name == candidate or name.endswith("." + candidate) or candidate.endswith("." + name):
            return name
    return None
