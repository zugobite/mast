"""Enterprise Python standards: typed public APIs, real exceptions, real logging."""

from __future__ import annotations

import ast
from typing import Iterator

from .context import FUNCTION_TYPES, ModuleContext, is_mutable_literal
from .model import Finding, check

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "critical", "exception", "log", "fatal"}
LOGGING_FUNCTIONS = {f"logging.{name}" for name in LOG_METHODS}
ABSTRACT_DECORATORS = {"abstractmethod", "abc.abstractmethod", "abstractproperty", "overload", "typing.overload"}


def _label(ctx: ModuleContext, func: ast.AST) -> str:
    klass = ctx.enclosing_class(func)
    name = getattr(func, "name", "<lambda>")
    return f"{klass.name}.{name}" if klass else name


@check(
    id="api/missing-type-hints",
    category="standards",
    severity="medium",
    title="Public function is not fully annotated",
    why=(
        "Type hints are the contract a caller reads and the only thing a type checker can verify. Without "
        "them refactors are guesswork across module boundaries, and a whole class of integration bug reaches "
        "runtime that mypy would have caught in CI."
    ),
    fix="Annotate every parameter and the return type. Use `-> None` for procedures and `Protocol`/`TypedDict` for structural inputs.",
)
def missing_type_hints(ctx: ModuleContext) -> Iterator[Finding]:
    if not ctx.settings.require_type_hints or ctx.is_test:
        return
    for func in ctx.functions():
        if not ctx.is_public(func.name) or ctx.enclosing_function(func) is not None:
            continue
        args = func.args
        positional = args.posonlyargs + args.args + args.kwonlyargs
        if positional and positional[0].arg in {"self", "cls"}:
            positional = positional[1:]
        missing = [a.arg for a in positional if a.annotation is None]
        wants_return = func.name not in {"__init__", "__post_init__"}
        if wants_return and func.returns is None:
            missing.append("return")
        if missing:
            detail = ", ".join(missing[:4]) + (", …" if len(missing) > 4 else "")
            yield ctx.report(func, f"`{_label(ctx, func)}()` is missing type hints for: {detail}", symbol=_label(ctx, func))


@check(
    id="api/missing-docstring",
    category="standards",
    severity="low",
    title="Public API has no docstring",
    why="The docstring is where the contract that types cannot express lives: units, error behaviour, side effects. Without it callers read the body and depend on incidental detail.",
    fix="Add a short docstring saying what it does, what it raises, and anything surprising. Skip the obvious getters.",
)
def missing_docstring(ctx: ModuleContext) -> Iterator[Finding]:
    if not ctx.settings.require_docstrings or ctx.is_test:
        return
    if ast.get_docstring(ctx.tree) is None and ctx.tree.body and not ctx.is_init:
        yield ctx.report(ctx.tree.body[0], f"module `{ctx.rel}` has no docstring")
    for node in ctx.nodes(ast.ClassDef, *FUNCTION_TYPES):
        name = node.name
        if not ctx.is_public(name) or ctx.enclosing_function(node) is not None:
            continue
        if ast.get_docstring(node) is None and not ctx.decorator_names(node) & {"property", "override", "overload"}:
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            yield ctx.report(node, f"public {kind} `{_label(ctx, node)}` has no docstring", symbol=_label(ctx, node))


@check(
    id="api/any-annotation",
    category="standards",
    severity="low",
    title="`Any` in a public signature",
    why="`Any` switches type checking off at that boundary, so errors pass straight through the layer that was supposed to validate them.",
    fix="Use a concrete type, a `Protocol` for duck typing, a `TypeVar` for generics, or `object` when you truly accept anything.",
)
def any_annotation(ctx: ModuleContext) -> Iterator[Finding]:
    if ctx.is_test:
        return
    for func in ctx.functions():
        if not ctx.is_public(func.name):
            continue
        annotations = [a.annotation for a in func.args.posonlyargs + func.args.args + func.args.kwonlyargs] + [func.returns]
        for annotation in annotations:
            if annotation is None:
                continue
            if any(isinstance(n, ast.Name) and n.id == "Any" for n in ast.walk(annotation)):
                yield ctx.report(annotation, f"`Any` in the signature of `{_label(ctx, func)}()` — use a concrete type or a Protocol", symbol=_label(ctx, func))
                break


@check(
    id="api/mutable-dataclass-default",
    category="standards",
    severity="medium",
    title="Mutable default on a dataclass field",
    why=(
        "A mutable default on a class-level field is shared by every instance that does not override it, so "
        "one object's mutation shows up in the next. `dataclass` rejects the obvious cases at import time and "
        "silently accepts the rest (custom objects, pydantic v1 models)."
    ),
    fix="Use `field(default_factory=list)` / `default_factory=dict` (or `Field(default_factory=...)` in pydantic).",
)
def mutable_dataclass_default(ctx: ModuleContext) -> Iterator[Finding]:
    for klass in ctx.nodes(ast.ClassDef):
        decorators = ctx.decorator_names(klass)
        bases = {(ctx.dotted(b) or "").rsplit(".", 1)[-1] for b in klass.bases}
        is_model = bool(decorators & {"dataclass", "dataclasses.dataclass", "define", "attrs.define", "attr.s"}) or bool(bases & {"BaseModel", "BaseSettings"})
        if not is_model:
            continue
        for stmt in klass.body:
            if not isinstance(stmt, ast.AnnAssign) or stmt.value is None:
                continue
            value = stmt.value
            if isinstance(value, ast.Call):
                factory = ctx.keyword(value, "default_factory")
                if factory is not None:
                    continue
                default = ctx.keyword(value, "default")
                value = default.value if default is not None else value
            if is_mutable_literal(value, ctx):
                name = ctx.dotted(stmt.target) or "field"
                yield ctx.report(stmt, f"`{klass.name}.{name}` has a mutable default shared by every instance — use `field(default_factory=...)`", symbol=f"{klass.name}.{name}")


@check(
    id="api/global-mutable-state",
    category="standards",
    severity="medium",
    title="Module-level mutable state",
    why=(
        "A module-level list or dict that the code mutates is process-wide shared state: it leaks between "
        "requests, breaks under threads and workers, and makes tests order-dependent — the classic "
        "'passes alone, fails in the suite'."
    ),
    fix="Pass the state explicitly, hold it on an instance, or use a proper cache/store with a defined lifetime.",
)
def global_mutable_state(ctx: ModuleContext) -> Iterator[Finding]:
    mutators = {"append", "extend", "insert", "remove", "pop", "clear", "add", "update", "discard", "setdefault"}
    candidates: dict[str, ast.stmt] = {}
    for stmt in ctx.tree.body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else ([stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
        for target in targets:
            name = target.id if isinstance(target, ast.Name) else None
            value = stmt.value
            if name and not name.isupper() and is_mutable_literal(value, ctx):
                candidates[name] = stmt
    if not candidates:
        return
    for name, stmt in candidates.items():
        mutated = False
        for node in ctx.all:
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in mutators:
                mutated = mutated or ctx.dotted(node.func.value) == name
            elif isinstance(node, (ast.Subscript,)) and isinstance(node.ctx, ast.Store):
                mutated = mutated or ctx.dotted(node.value) == name
            elif isinstance(node, ast.Global) and name in node.names:
                mutated = True
        if mutated:
            yield ctx.report(stmt, f"module-level `{name}` is mutated at runtime — shared process state leaks between requests and tests", symbol=name)


@check(
    id="api/unfinished-code",
    category="standards",
    severity="medium",
    title="Unfinished implementation left behind",
    why="A stub that raises `NotImplementedError` or silently passes looks complete to the caller and to review, then fails in production on the first real input.",
    fix="Finish it, or keep it abstract (`@abstractmethod` on an ABC/Protocol) so the type checker refuses incomplete subclasses.",
)
def unfinished_code(ctx: ModuleContext) -> Iterator[Finding]:
    if ctx.is_test:
        return
    for func in ctx.functions():
        if ctx.decorator_names(func) & ABSTRACT_DECORATORS:
            continue
        klass = ctx.enclosing_class(func)
        bases = {(ctx.dotted(b) or "").rsplit(".", 1)[-1] for b in klass.bases} if klass else set()
        if bases & {"ABC", "Protocol"}:
            continue
        body = [s for s in func.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
        raises_stub = any(
            isinstance(s, ast.Raise)
            and (ctx.dotted(s.exc.func if isinstance(s.exc, ast.Call) else s.exc) or "").endswith("NotImplementedError")
            for s in body
        )
        if raises_stub and len(body) == 1:
            yield ctx.report(func, f"`{_label(ctx, func)}()` is an unimplemented stub — finish it or mark it `@abstractmethod`", symbol=_label(ctx, func))


@check(
    id="api/sys-exit-in-library",
    category="standards",
    severity="medium",
    title="`sys.exit()` in library code",
    why="Killing the process from inside a library takes the decision away from the caller: a web worker, test runner or notebook is terminated instead of handling the error.",
    fix="Raise an exception and let the entry point (CLI `main`, request handler) decide the exit code.",
)
def sys_exit_in_library(ctx: ModuleContext) -> Iterator[Finding]:
    if ctx.is_script or ctx.is_test:
        return
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        if name in {"sys.exit", "os._exit", "exit", "quit"} or name.endswith(".sys.exit"):
            if ctx.enclosing(call, ast.If) and ctx.is_module_level(call):
                continue
            yield ctx.report(call, f"`{name}()` in library code — raise an exception and let the entry point exit")


@check(
    id="err/broad-except",
    category="standards",
    severity="medium",
    title="`except Exception` without handling it",
    why=(
        "Catching everything and carrying on hides programming errors — a typo'd attribute is treated like a "
        "network timeout. The system keeps running in an undefined state instead of failing where the fault is."
    ),
    fix="Catch the specific exceptions you can recover from. If you must catch broadly, log with `logger.exception(...)` and re-raise or return an explicit error.",
)
def broad_except(ctx: ModuleContext) -> Iterator[Finding]:
    for handler in ctx.nodes(ast.ExceptHandler):
        name = ctx.dotted(handler.type) or ""
        if name.rsplit(".", 1)[-1] not in {"Exception", "BaseException"}:
            continue
        handled = False
        for node in ast.walk(handler):
            if isinstance(node, ast.Raise):
                handled = True
            elif isinstance(node, ast.Call):
                call_name = ctx.dotted(node.func) or ""
                if call_name.rsplit(".", 1)[-1] in LOG_METHODS or "capture" in call_name:
                    handled = True
            elif handler.name and isinstance(node, ast.Name) and node.id == handler.name:
                handled = True  # the error is recorded or returned, not discarded
        if not handled:
            yield ctx.report(handler, f"`except {name}` neither logs nor re-raises — real errors disappear here")


@check(
    id="err/bare-exception-class",
    category="standards",
    severity="medium",
    title="Generic `Exception` raised",
    why=(
        "`raise Exception('failed')` forces every caller to catch everything or parse the message. Callers "
        "cannot distinguish a validation problem from an outage, so retry and error-reporting logic cannot be written."
    ),
    fix="Raise a specific builtin (`ValueError`, `PermissionError`, `TimeoutError`) or a domain exception from your own base class.",
)
def bare_exception_class(ctx: ModuleContext) -> Iterator[Finding]:
    for node in ctx.nodes(ast.Raise):
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        name = (ctx.dotted(target) or "").rsplit(".", 1)[-1]
        if name in {"Exception", "BaseException"}:
            yield ctx.report(node, f"`raise {name}` gives callers nothing to branch on — raise a specific exception type")


@check(
    id="err/lost-traceback",
    category="standards",
    severity="medium",
    title="Exception re-raised without `from`",
    why=(
        "Raising a new exception inside an `except` block without `from` hides the original cause. The "
        "traceback shows your wrapper and not the line that actually failed, which is exactly the line you "
        "need at 3am."
    ),
    fix="Use `raise MyError(...) from exc` — or `from None` when hiding the cause is deliberate.",
)
def lost_traceback(ctx: ModuleContext) -> Iterator[Finding]:
    for handler in ctx.nodes(ast.ExceptHandler):
        for node in ast.walk(handler):
            if not isinstance(node, ast.Raise) or node.cause is not None or node.exc is None:
                continue
            if isinstance(node.exc, ast.Name) and handler.name and node.exc.id == handler.name:
                continue  # bare re-raise of the same exception keeps the traceback
            if ctx.enclosing(node, ast.ExceptHandler) is not handler:
                continue
            yield ctx.report(node, "exception raised inside `except` without `from` — the original traceback is lost")


@check(
    id="log/print-in-code",
    category="standards",
    severity="medium",
    title="`print()` used for output in application code",
    why=(
        "`print` has no level, no timestamp, no request context and no destination control, so it is invisible "
        "to log aggregation and cannot be turned down. In a service it is output nobody will ever see."
    ),
    fix="Use a module logger: `logger = logging.getLogger(__name__)` and `logger.info(...)`. Keep `print` for CLI output.",
)
def print_in_code(ctx: ModuleContext) -> Iterator[Finding]:
    if ctx.is_test or ctx.is_script:
        return
    for call in ctx.nodes(ast.Call):
        if (ctx.dotted(call.func) or "") == "print":
            yield ctx.report(call, "`print()` in application code — use the module logger instead")


@check(
    id="log/root-logger",
    category="standards",
    severity="medium",
    title="Root logger used directly",
    why=(
        "`logging.info(...)` writes to the root logger, so the message carries no module name and cannot be "
        "filtered or levelled per component. It also configures logging as a side effect of the first call."
    ),
    fix="Create a module logger once: `logger = logging.getLogger(__name__)`.",
)
def root_logger(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        if name in LOGGING_FUNCTIONS:
            yield ctx.report(call, f"`{name}()` logs to the root logger — use `logging.getLogger(__name__)`")


@check(
    id="log/eager-formatting",
    category="standards",
    severity="low",
    title="Log message formatted before the level check",
    why=(
        "An f-string is built whether or not the level is enabled, so debug logging costs CPU in production. "
        "It also loses the structured arguments that log aggregators group duplicate messages by."
    ),
    fix="Pass the values as arguments: `logger.info(\"user %s saved\", user_id)`.",
)
def eager_formatting(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        if not isinstance(call.func, ast.Attribute) or call.func.attr not in LOG_METHODS:
            continue
        if "log" not in (ctx.dotted(call.func.value) or "").lower():
            continue
        for arg in call.args[:2]:
            if isinstance(arg, ast.JoinedStr) or (isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Mod, ast.Add))):
                yield ctx.report(arg, f"`logger.{call.func.attr}()` formats its message eagerly — pass the values as arguments instead")
                break


@check(
    id="log/error-without-traceback",
    category="standards",
    severity="medium",
    title="Error logged without its traceback",
    why=(
        "`logger.error(str(exc))` records one line and throws the stack away, so the log says what broke but "
        "not where. Debugging then needs a reproduction instead of the log you already have."
    ),
    fix="Inside an `except` block use `logger.exception(\"...\")` (or `logger.error(..., exc_info=True)`).",
)
def error_without_traceback(ctx: ModuleContext) -> Iterator[Finding]:
    for handler in ctx.nodes(ast.ExceptHandler):
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"error", "critical", "warning", "fatal"}:
                continue
            if "log" not in (ctx.dotted(node.func.value) or "").lower():
                continue
            if ctx.keyword(node, "exc_info") is not None:
                continue
            mentions_exception = handler.name and any(
                isinstance(n, ast.Name) and n.id == handler.name for n in ast.walk(node)
            )
            if mentions_exception:
                yield ctx.report(node, f"`logger.{node.func.attr}()` in an `except` block drops the traceback — use `logger.exception(...)`")
