"""Bug hunting: defects that make the code do the wrong thing at runtime."""

from __future__ import annotations

import ast
import re
from typing import Iterator

from .context import (
    COMPREHENSION_TYPES,
    FUNCTION_TYPES,
    LOOP_TYPES,
    ModuleContext,
    is_mutable_literal,
)
from .model import Finding, check

BUILTIN_EXCEPTIONS = {
    name
    for name in dir(__builtins__ if isinstance(__builtins__, dict) else __builtins__)
    if name.endswith(("Error", "Exception", "Warning", "Exit", "Interrupt"))
} or {"ValueError", "TypeError", "KeyError", "RuntimeError", "Exception"}

# Handlers listed earlier that already swallow anything a later handler wants.
EXCEPTION_PARENTS = {
    "BaseException": set(BUILTIN_EXCEPTIONS),
    "Exception": {
        "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError", "RuntimeError",
        "OSError", "IOError", "LookupError", "ArithmeticError", "ZeroDivisionError", "StopIteration",
        "NotImplementedError", "ImportError", "ModuleNotFoundError", "UnicodeError", "NameError",
        "FileNotFoundError", "PermissionError", "TimeoutError", "ConnectionError", "JSONDecodeError",
    },
    "OSError": {"FileNotFoundError", "PermissionError", "TimeoutError", "ConnectionError", "IsADirectoryError", "FileExistsError"},
    "LookupError": {"KeyError", "IndexError"},
    "ArithmeticError": {"ZeroDivisionError", "OverflowError", "FloatingPointError"},
    "ValueError": {"UnicodeError", "UnicodeDecodeError", "JSONDecodeError"},
    "ImportError": {"ModuleNotFoundError"},
}

BLOCKING_CALLS = {
    "time.sleep": "time.sleep blocks the whole event loop — use `await asyncio.sleep(...)`",
    "requests.get": "requests is synchronous — use httpx.AsyncClient or aiohttp",
    "requests.post": "requests is synchronous — use httpx.AsyncClient or aiohttp",
    "requests.put": "requests is synchronous — use httpx.AsyncClient or aiohttp",
    "requests.patch": "requests is synchronous — use httpx.AsyncClient or aiohttp",
    "requests.delete": "requests is synchronous — use httpx.AsyncClient or aiohttp",
    "requests.head": "requests is synchronous — use httpx.AsyncClient or aiohttp",
    "requests.request": "requests is synchronous — use httpx.AsyncClient or aiohttp",
    "urllib.request.urlopen": "urlopen blocks — use an async HTTP client",
    "subprocess.run": "subprocess.run blocks — use asyncio.create_subprocess_exec",
    "subprocess.call": "subprocess.call blocks — use asyncio.create_subprocess_exec",
    "subprocess.check_call": "subprocess.check_call blocks — use asyncio.create_subprocess_exec",
    "subprocess.check_output": "subprocess.check_output blocks — use asyncio.create_subprocess_exec",
    "os.system": "os.system blocks — use asyncio.create_subprocess_exec",
}

MUTATORS = {"append", "extend", "insert", "remove", "pop", "clear", "add", "update", "discard", "sort", "setdefault", "popitem"}
SAFE_ITER_WRAPPERS = {"list", "tuple", "set", "sorted", "copy", "deepcopy", "reversed", "frozenset", "dict"}


@check(
    id="bug/syntax-error",
    category="bugs",
    severity="high",
    title="File does not parse",
    why="A module with a syntax error cannot be imported, tested or deployed — every other check is blind on it.",
    fix="Fix the syntax error the message points at. The analyzer reports the position Python itself rejects.",
)
def syntax_error(ctx: ModuleContext) -> Iterator[Finding]:
    """Emitted by the engine when a file fails to parse; never runs over a tree."""
    return iter(())


@check(
    id="bug/mutable-default",
    category="bugs",
    severity="high",
    title="Mutable default argument",
    why=(
        "Default arguments are evaluated once, when the function is defined. A list, dict or set default is "
        "therefore shared by every call, so state leaks between unrelated callers — a classic source of "
        "'impossible' production bugs that tests with a single call never reproduce."
    ),
    fix="Default to `None` and build the container inside the body: `def f(items=None): items = [] if items is None else items`.",
)
def mutable_default(ctx: ModuleContext) -> Iterator[Finding]:
    for func in ctx.functions():
        args = func.args
        positional = args.posonlyargs + args.args
        pairs = list(zip(positional[len(positional) - len(args.defaults):], args.defaults))
        pairs += list(zip(args.kwonlyargs, args.kw_defaults))
        for arg, default in pairs:
            if default is not None and is_mutable_literal(default, ctx):
                yield ctx.report(
                    default,
                    f"`{func.name}(..., {arg.arg}={ctx.segment(default) or '...'})` is a mutable default — the same object is shared by every call",
                    symbol=f"{func.name}.{arg.arg}",
                )


@check(
    id="bug/late-binding-closure",
    category="bugs",
    severity="high",
    title="Closure captures the loop variable",
    why=(
        "A function defined in a loop captures the loop variable by reference, not by value. Every closure "
        "created in the loop sees the final value, so callbacks, partials and deferred tasks all act on the "
        "last item instead of the one they were created for."
    ),
    fix="Bind the value at definition time — `lambda x=item: f(x)` — or use `functools.partial(f, item)`.",
)
def late_binding_closure(ctx: ModuleContext) -> Iterator[Finding]:
    scopes: list[tuple[ast.AST, set[str]]] = []
    for loop in ctx.nodes(ast.For, ast.AsyncFor):
        scopes.append((loop, {n.id for n in ast.walk(loop.target) if isinstance(n, ast.Name)}))
    for comp in ctx.nodes(*COMPREHENSION_TYPES):
        targets = {n.id for gen in comp.generators for n in ast.walk(gen.target) if isinstance(n, ast.Name)}
        scopes.append((comp, targets))
    for loop, targets in scopes:
        if not targets:
            continue
        for node in ast.walk(loop):
            if node is loop or not isinstance(node, (ast.Lambda,) + FUNCTION_TYPES):
                continue
            bound = {a.arg for a in node.args.args + node.args.kwonlyargs if a is not None}
            defaulted = {
                a.arg
                for a, d in zip(node.args.args[::-1], node.args.defaults[::-1])
                if d is not None
            }
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            leaked = (targets & used) - (bound - defaulted) - defaulted
            # Only a closure that outlives the iteration can observe the final value.
            if not _escapes(ctx, node):
                continue
            for name in sorted(leaked):
                yield ctx.report(
                    node,
                    f"closure captures loop variable `{name}` by reference — every closure will see its last value",
                    symbol=getattr(node, "name", "<lambda>"),
                )
                break


DEFERRING_CALLS = {
    "append", "add", "insert", "extend", "put", "submit", "partial", "add_done_callback", "connect",
    "subscribe", "on", "bind", "after", "schedule", "enqueue", "delay", "Thread", "Timer", "Process",
    "create_task", "ensure_future", "register", "setdefault", "sorted", "map", "filter",
}


def _escapes(ctx: ModuleContext, node: ast.AST) -> bool:
    """True when the function object is stored, returned or handed to something that defers it."""
    parent = ctx.parent(node)
    if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.Return, ast.Yield, ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return True
    if isinstance(parent, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        return True
    if isinstance(parent, ast.keyword):
        return True
    if isinstance(parent, ast.Call):
        name = (ctx.dotted(parent.func) or "").rsplit(".", 1)[-1]
        return name in DEFERRING_CALLS
    return False


@check(
    id="bug/bare-except",
    category="bugs",
    severity="high",
    title="Bare `except:`",
    why=(
        "A bare except also catches KeyboardInterrupt and SystemExit, so Ctrl-C and process shutdown are "
        "swallowed, and genuine programming errors are hidden instead of surfacing."
    ),
    fix="Catch what you can handle: `except (ValueError, KeyError):`, or `except Exception:` when you truly mean any application error.",
)
def bare_except(ctx: ModuleContext) -> Iterator[Finding]:
    for handler in ctx.nodes(ast.ExceptHandler):
        if handler.type is None:
            yield ctx.report(handler, "bare `except:` also catches KeyboardInterrupt and SystemExit")


@check(
    id="bug/silent-except",
    category="bugs",
    severity="medium",
    title="Exception silently swallowed",
    why=(
        "An except block that only passes discards the error and its traceback. The failure still happened, "
        "but nothing records it — the bug resurfaces later as corrupt data or an empty screen with no trail."
    ),
    fix="Log it (`logger.exception(...)`), re-raise, or return an explicit fallback. If ignoring is correct, say why in a comment.",
)
def silent_except(ctx: ModuleContext) -> Iterator[Finding]:
    optional_import = {"ImportError", "ModuleNotFoundError"}
    for handler in ctx.nodes(ast.ExceptHandler):
        body = [s for s in handler.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
        if not body or not all(isinstance(s, (ast.Pass, ast.Continue)) for s in body):
            continue
        caught = {ctx.dotted(handler.type) or ""} if handler.type else set()
        if isinstance(handler.type, ast.Tuple):
            caught = {ctx.dotted(e) or "" for e in handler.type.elts}
        if caught and caught <= optional_import:
            continue  # `except ImportError: pass` is the idiom for an optional dependency.
        if _has_comment(ctx, handler):
            continue  # the rule asks for a reason; a comment is one.
        yield ctx.report(handler, "exception is caught and silently discarded — log it, re-raise it, or explain in a comment why it is safe to ignore")


def _has_comment(ctx: ModuleContext, node: ast.AST) -> bool:
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start) or start
    return any("#" in ctx.line_text(line) for line in range(start, end + 1))


@check(
    id="bug/handler-order",
    category="bugs",
    severity="high",
    title="Unreachable exception handler",
    why=(
        "Handlers are tried in order, so a broad handler listed first catches everything a narrower one below "
        "it was written for. The specific recovery code never runs — and nobody notices until the specific "
        "failure happens in production."
    ),
    fix="Order handlers narrowest first, with `except Exception:` last.",
)
def handler_order(ctx: ModuleContext) -> Iterator[Finding]:
    for tried in ctx.nodes(ast.Try):
        seen: set[str] = set()
        for handler in tried.handlers:
            names = []
            if isinstance(handler.type, ast.Tuple):
                names = [ctx.dotted(e) or "" for e in handler.type.elts]
            elif handler.type is not None:
                names = [ctx.dotted(handler.type) or ""]
            for name in names:
                tail = name.rsplit(".", 1)[-1]
                for earlier in seen:
                    if tail == earlier or tail in EXCEPTION_PARENTS.get(earlier, ()):
                        yield ctx.report(
                            handler,
                            f"`except {tail}` is unreachable — `except {earlier}` above already catches it",
                        )
                        break
            seen.update(n.rsplit(".", 1)[-1] for n in names)
            if handler.type is None:
                seen.add("BaseException")


@check(
    id="bug/assert-for-validation",
    category="bugs",
    severity="high",
    title="`assert` used outside tests",
    why=(
        "Assertions are removed when Python runs with -O, which is common in production images. Validation "
        "written as an assert therefore disappears exactly where it matters, and untrusted input flows "
        "straight through."
    ),
    fix="Raise explicitly: `if not ok: raise ValueError(...)`. Keep `assert` for tests and for invariants you are happy to lose.",
)
def assert_for_validation(ctx: ModuleContext) -> Iterator[Finding]:
    if ctx.is_test:
        return
    for node in ctx.nodes(ast.Assert):
        if isinstance(node.test, ast.Constant):
            continue
        yield ctx.report(node, "`assert` is stripped under `python -O` — raise an explicit exception instead")


@check(
    id="bug/identity-comparison",
    category="bugs",
    severity="high",
    title="`is` compared against a literal",
    why=(
        "`is` compares object identity, not value. It appears to work for small ints and short strings "
        "because CPython interns them, then fails for the same value computed a different way — a bug that "
        "only shows up with real data."
    ),
    fix="Use `==` for values; keep `is` for `None`, `True`, `False` and sentinels.",
)
def identity_comparison(ctx: ModuleContext) -> Iterator[Finding]:
    for compare in ctx.nodes(ast.Compare):
        for op, comparator in zip(compare.ops, compare.comparators):
            if not isinstance(op, (ast.Is, ast.IsNot)):
                continue
            for side in (compare.left, comparator):
                sentinel = isinstance(side, ast.Constant) and side.value in (Ellipsis, NotImplemented)
                literal = (
                    isinstance(side, ast.Constant)
                    and not isinstance(side.value, bool)
                    and side.value is not None
                    and not sentinel
                )
                if literal or isinstance(side, (ast.List, ast.Dict, ast.Set, ast.Tuple, ast.JoinedStr)):
                    yield ctx.report(compare, "`is` compares identity, not value — use `==` for literals")
                    break


@check(
    id="bug/mutate-during-iteration",
    category="bugs",
    severity="high",
    title="Collection mutated while iterating it",
    why=(
        "Adding to or removing from a list, dict or set while looping over it skips elements or raises "
        "`RuntimeError: dictionary changed size during iteration`. The skipped items are silent — the loop "
        "simply misses records."
    ),
    fix="Iterate over a copy (`for x in list(items):`) or build the result in a new collection and replace it afterwards.",
)
def mutate_during_iteration(ctx: ModuleContext) -> Iterator[Finding]:
    for loop in ctx.nodes(ast.For, ast.AsyncFor):
        iterated = loop.iter
        if isinstance(iterated, ast.Call):
            name = ctx.call_name(iterated) or ""
            tail = name.rsplit(".", 1)[-1]
            if tail in SAFE_ITER_WRAPPERS:
                continue  # iterating a copy is the fix, not the bug
            if tail in {"items", "keys", "values"} and isinstance(iterated.func, ast.Attribute):
                iterated = iterated.func.value
            else:
                continue
        target = ctx.dotted(iterated)
        if not target:
            continue
        for node in ast.walk(loop):
            hit = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS:
                if ctx.dotted(node.func.value) == target:
                    hit = f"{target}.{node.func.attr}()"
            elif isinstance(node, ast.Delete):
                for victim in node.targets:
                    if isinstance(victim, ast.Subscript) and ctx.dotted(victim.value) == target:
                        hit = f"del {target}[...]"
            if hit:
                yield ctx.report(node, f"`{hit}` mutates `{target}` while the loop iterates it — iterate over a copy instead")


@check(
    id="bug/blocking-call-in-async",
    category="bugs",
    severity="high",
    title="Blocking call inside `async def`",
    why=(
        "A synchronous call inside a coroutine blocks the event loop, freezing every other request in the "
        "process. Under load this looks like a mysterious latency cliff rather than an error."
    ),
    fix="Use the async equivalent, or push the blocking work to a thread with `await asyncio.to_thread(...)`.",
)
def blocking_call_in_async(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = ctx.call_name(call)
        if not name:
            continue
        hint = BLOCKING_CALLS.get(name) or BLOCKING_CALLS.get(name.rsplit(".", 2)[-1])
        if not hint:
            for blocking, text in BLOCKING_CALLS.items():
                if name.endswith("." + blocking) or name == blocking:
                    hint = text
                    break
        if not hint:
            continue
        func = ctx.in_async_function(call)
        if func is None:
            continue
        yield ctx.report(call, f"blocking call in async `{func.name}()` — {hint}", symbol=func.name)


@check(
    id="bug/unraised-exception",
    category="bugs",
    severity="high",
    title="Exception created but not raised",
    why=(
        "Constructing an exception has no effect on its own. The error path silently continues as if "
        "everything succeeded, and the caller gets a success result for a failed operation."
    ),
    fix="Add `raise`: `raise ValueError(...)`.",
)
def unraised_exception(ctx: ModuleContext) -> Iterator[Finding]:
    for node in ctx.nodes(ast.Expr):
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        name = ctx.dotted(call.func) or ""
        tail = name.rsplit(".", 1)[-1]
        if tail.endswith(("Error", "Exception")) and tail[:1].isupper():
            yield ctx.report(node, f"`{tail}(...)` is created but never raised — add `raise`")


@check(
    id="bug/unreachable-code",
    category="bugs",
    severity="medium",
    title="Unreachable statement",
    why=(
        "Code after a `return`, `raise`, `continue` or `break` in the same block never executes. It is "
        "usually a real mistake — cleanup, a log line or the actual result left stranded behind an early exit."
    ),
    fix="Move the statement above the exit, or delete it if it is dead.",
)
def unreachable_code(ctx: ModuleContext) -> Iterator[Finding]:
    for node in ctx.all:
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if not isinstance(body, list):
                continue
            for index, stmt in enumerate(body[:-1]):
                if isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                    follower = body[index + 1]
                    kind = type(stmt).__name__.lower()
                    yield ctx.report(follower, f"unreachable code — the `{kind}` above always exits this block")
                    break


@check(
    id="bug/return-in-finally",
    category="bugs",
    severity="high",
    title="`return` or `break` in a `finally` block",
    why=(
        "A `finally` block that returns or breaks discards any exception in flight. Failures vanish without "
        "a traceback and callers see a normal return value for work that never completed."
    ),
    fix="Put the value in a variable and return it after the `try`, leaving `finally` for cleanup only.",
)
def return_in_finally(ctx: ModuleContext) -> Iterator[Finding]:
    for tried in ctx.nodes(ast.Try):
        for stmt in tried.finalbody:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Return) and ctx.enclosing_function(node) is ctx.enclosing_function(tried):
                    yield ctx.report(node, "`return` in `finally` swallows any exception raised in the `try` block")
                elif isinstance(node, (ast.Break, ast.Continue)) and not any(
                    isinstance(p, LOOP_TYPES) for p in ctx.ancestors(node) if p is not tried
                ):
                    yield ctx.report(node, f"`{type(node).__name__.lower()}` in `finally` swallows any exception raised in the `try` block")


def _is_condition(ctx: ModuleContext, node: ast.AST) -> bool:
    """True when this expression is tested as a boolean rather than used as a value."""
    parent = ctx.parent(node)
    while isinstance(parent, (ast.BoolOp, ast.UnaryOp)):
        node, parent = parent, ctx.parent(parent)
    if isinstance(parent, (ast.If, ast.While, ast.IfExp)):
        return getattr(parent, "test", None) is node
    if isinstance(parent, ast.Assert):
        return parent.test is node
    if isinstance(parent, ast.comprehension):
        return node in parent.ifs
    return False


@check(
    id="bug/constant-in-condition",
    category="bugs",
    severity="high",
    title="Literal used as a condition",
    why=(
        "`if x == 1 or 2:` reads like 'x is 1 or 2' but means 'x == 1, or the truthy value 2' — always true. "
        "The branch fires for every input and the intended comparison never happens."
    ),
    fix="Compare explicitly (`x == 1 or x == 2`) or test membership (`x in (1, 2)`).",
)
def constant_in_condition(ctx: ModuleContext) -> Iterator[Finding]:
    for boolop in ctx.nodes(ast.BoolOp):
        # `value = x or "default"` is idiomatic; `if x == 1 or 2:` is a bug.
        if not _is_condition(ctx, boolop):
            continue
        if not any(isinstance(v, ast.Compare) for v in boolop.values):
            continue
        for value in boolop.values:
            if isinstance(value, ast.Constant) and value.value not in (None, True, False) and value.value:
                op = "or" if isinstance(boolop.op, ast.Or) else "and"
                yield ctx.report(
                    boolop,
                    f"literal `{value.value!r}` as a `{op}` operand is always truthy — this condition does not compare what it looks like",
                )
                break


@check(
    id="bug/self-comparison",
    category="bugs",
    severity="medium",
    title="Expression compared with itself",
    why="Comparing a value with itself is always the same answer, so the guard it protects is either dead or the wrong variable was typed.",
    fix="Compare against the value you meant — usually a neighbouring variable or attribute.",
)
def self_comparison(ctx: ModuleContext) -> Iterator[Finding]:
    for compare in ctx.nodes(ast.Compare):
        if len(compare.ops) != 1 or isinstance(compare.ops[0], (ast.In, ast.NotIn)):
            continue
        left, right = compare.left, compare.comparators[0]
        if isinstance(left, (ast.Name, ast.Attribute)) and ast.dump(left) == ast.dump(right):
            yield ctx.report(compare, f"`{ctx.dotted(left)}` is compared with itself — did you mean a different operand?")


@check(
    id="bug/self-assignment",
    category="bugs",
    severity="medium",
    title="Variable assigned to itself",
    why="A self-assignment does nothing. It usually means an attribute, parameter or index on one side was mistyped, so the real value is never stored.",
    fix="Assign the value you meant, e.g. `self.total = total`.",
)
def self_assignment(ctx: ModuleContext) -> Iterator[Finding]:
    for assign in ctx.nodes(ast.Assign):
        if len(assign.targets) != 1:
            continue
        target = assign.targets[0]
        if (
            isinstance(target, (ast.Name, ast.Attribute))
            and isinstance(assign.value, (ast.Name, ast.Attribute))
            and ctx.dotted(target) == ctx.dotted(assign.value)
        ):
            yield ctx.report(assign, f"`{ctx.dotted(target)}` is assigned to itself — no value is stored")


@check(
    id="bug/duplicate-condition",
    category="bugs",
    severity="high",
    title="Duplicated branch condition",
    why="The second identical condition in an if/elif chain can never be reached, so the code it guards is dead and the case it was meant to handle falls through.",
    fix="Correct the duplicated test, or merge the branches if they really are the same case.",
)
def duplicate_condition(ctx: ModuleContext) -> Iterator[Finding]:
    for node in ctx.nodes(ast.If):
        parent = ctx.parent(node)
        if isinstance(parent, ast.If) and node in parent.orelse:
            continue  # only report from the top of a chain
        seen: dict[str, int] = {}
        current: ast.If | None = node
        while isinstance(current, ast.If):
            key = ast.dump(current.test)
            if key in seen:
                yield ctx.report(current, f"this condition repeats the one on line {seen[key]} — the branch can never run")
            else:
                seen[key] = current.test.lineno
            current = current.orelse[0] if len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If) else None


@check(
    id="bug/duplicate-key",
    category="bugs",
    severity="medium",
    title="Duplicate key in a literal",
    why="A repeated key in a dict or set literal silently discards the earlier entry, so configuration or mapping data quietly loses a value.",
    fix="Remove or rename the duplicate.",
)
def duplicate_key(ctx: ModuleContext) -> Iterator[Finding]:
    for node in ctx.nodes(ast.Dict, ast.Set):
        items = node.keys if isinstance(node, ast.Dict) else node.elts
        seen: dict[object, int] = {}
        for item in items:
            if not isinstance(item, ast.Constant):
                continue
            key = (type(item.value).__name__, item.value)
            if key in seen:
                kind = "key" if isinstance(node, ast.Dict) else "element"
                yield ctx.report(item, f"duplicate {kind} {item.value!r} — the earlier entry on line {seen[key]} is discarded")
            else:
                seen[key] = item.lineno


@check(
    id="bug/missing-f-prefix",
    category="bugs",
    severity="medium",
    title="Placeholder in a plain string",
    why=(
        "A string containing `{name}` without the `f` prefix is never interpolated. Logs, messages and "
        "queries ship the literal braces instead of the value, so the useful detail is lost."
    ),
    fix="Add the `f` prefix, or call `.format(...)` if the template is filled in later.",
)
def missing_f_prefix(ctx: ModuleContext) -> Iterator[Finding]:
    placeholder = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?:[.\[][^{}]*)?(?::[^{}]*)?\}")
    for node in ctx.nodes(ast.Constant):
        if not isinstance(node.value, str) or "{" not in node.value:
            continue
        parent = ctx.parent(node)
        if isinstance(parent, ast.Expr):
            continue  # docstring or a deliberate template literal
        if isinstance(parent, ast.Attribute) and parent.attr in {"format", "format_map"}:
            continue
        if isinstance(parent, ast.Call):
            callee = (ctx.dotted(parent.func) or "").rsplit(".", 1)[-1]
            # `"{file}"` passed to replace/split/sub is a template, not a broken f-string.
            if callee in {"replace", "split", "rsplit", "join", "startswith", "endswith", "count", "find",
                          "index", "sub", "subn", "match", "search", "fullmatch", "compile", "strip",
                          "lstrip", "rstrip", "partition", "format", "format_map", "removeprefix", "removesuffix"}:
                continue
        if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Mod):
            continue
        names = {m.group(1) for m in placeholder.finditer(node.value)}
        if not names:
            continue
        scope = ctx.enclosing_function(node) or ctx.tree
        in_scope = {n.id for n in ast.walk(scope) if isinstance(n, ast.Name)}
        if isinstance(scope, FUNCTION_TYPES):
            in_scope |= {a.arg for a in scope.args.args}
        hit = sorted(names & in_scope)
        if hit:
            yield ctx.report(node, f"string uses `{{{hit[0]}}}` but has no `f` prefix — it will not be interpolated")


@check(
    id="bug/implicit-string-concat",
    category="bugs",
    severity="medium",
    title="Implicit string concatenation — probably a missing comma",
    why=(
        "Two adjacent string literals are joined into one, so a missing comma in a list silently merges two "
        "entries. The collection ends up one element short with a corrupted value, and nothing raises."
    ),
    fix="Add the missing comma. When the join is deliberate, end the first part with a space so the intent is visible, or use explicit `+`.",
)
def implicit_string_concat(ctx: ModuleContext) -> Iterator[Finding]:
    import io
    import tokenize

    try:
        tokens = [
            token
            for token in tokenize.generate_tokens(io.StringIO(ctx.source).readline)
            if token.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)
        ]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return
    for first, second in zip(tokens, tokens[1:]):
        if first.type != tokenize.STRING or second.type != tokenize.STRING:
            continue
        try:
            value = ast.literal_eval(first.string)
        except (ValueError, SyntaxError):
            continue  # an unparseable literal (e.g. an f-string) tells us nothing here
        # A deliberate split almost always ends mid-sentence: a trailing space, comma or hyphen.
        if not isinstance(value, str) or not value or value[-1] in " \t\n,-(;:[{/|\\":
            continue
        yield ctx.report(
            _At(second.start[0], second.start[1]),
            "adjacent string literals are concatenated into one — a comma is probably missing",
        )


class _At:
    """A position stand-in for findings that come from tokens rather than AST nodes."""

    def __init__(self, lineno: int, col_offset: int) -> None:
        self.lineno = lineno
        self.col_offset = col_offset
        self.end_lineno = lineno


@check(
    id="bug/missing-super-init",
    category="bugs",
    severity="medium",
    title="Subclass `__init__` never calls `super().__init__()`",
    why=(
        "Skipping the parent initialiser leaves the base class's attributes unset. The object looks valid "
        "until a base-class method runs and fails on missing state, often far from this file."
    ),
    fix="Call `super().__init__(...)` — usually first — in the subclass `__init__`.",
)
def missing_super_init(ctx: ModuleContext) -> Iterator[Finding]:
    skip_bases = {"object", "Protocol", "TypedDict", "Enum", "IntEnum", "StrEnum", "NamedTuple", "Generic", "ABC"}
    skip_decorators = {"dataclass", "dataclasses.dataclass", "attr.s", "attrs.define", "define"}
    for klass in ctx.nodes(ast.ClassDef):
        bases = [ctx.dotted(b) or "" for b in klass.bases]
        concrete = [b for b in bases if b.rsplit(".", 1)[-1] not in skip_bases]
        if not concrete or ctx.decorator_names(klass) & skip_decorators:
            continue
        if any(b.rsplit(".", 1)[-1].endswith(("Error", "Exception")) for b in concrete):
            continue
        init = next((n for n in klass.body if isinstance(n, FUNCTION_TYPES) and n.name == "__init__"), None)
        if init is None:
            continue
        calls_super = any(
            (ctx.dotted(call.func) or "").endswith("__init__")
            or (isinstance(call.func, ast.Attribute) and call.func.attr == "__init__")
            for call in ast.walk(init)
            if isinstance(call, ast.Call)
        )
        if not calls_super:
            yield ctx.report(
                init,
                f"`{klass.name}.__init__` never calls `super().__init__()` — base state from {concrete[0]} stays uninitialised",
                symbol=f"{klass.name}.__init__",
            )


@check(
    id="bug/shared-class-attribute",
    category="bugs",
    severity="medium",
    title="Mutable class attribute shared by all instances",
    why=(
        "A list or dict defined in the class body belongs to the class, not the instance. Every object "
        "appends to the same container, so data from one request, user or tenant shows up in another."
    ),
    fix="Assign it in `__init__`, or use `dataclasses.field(default_factory=list)`.",
)
def shared_class_attribute(ctx: ModuleContext) -> Iterator[Finding]:
    for klass in ctx.nodes(ast.ClassDef):
        for stmt in klass.body:
            if isinstance(stmt, ast.AnnAssign):
                name_node, value = stmt.target, stmt.value
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                name_node, value = stmt.targets[0], stmt.value
            else:
                continue
            name = ctx.dotted(name_node) or ""
            if not name or name.isupper() or not is_mutable_literal(value, ctx):
                continue
            yield ctx.report(
                stmt,
                f"`{klass.name}.{name}` is a mutable class attribute shared by every instance — set it in `__init__` or use `field(default_factory=...)`",
                symbol=f"{klass.name}.{name}",
            )


@check(
    id="bug/naive-utcnow",
    category="bugs",
    severity="medium",
    title="Naive UTC timestamp",
    why=(
        "`datetime.utcnow()` returns a naive datetime that claims no timezone while holding UTC. Compared "
        "with an aware timestamp it raises, and stored in a local-time column it silently shifts by hours. "
        "It is deprecated from Python 3.12."
    ),
    fix="Use `datetime.now(timezone.utc)` (or `datetime.fromtimestamp(ts, timezone.utc)`) and keep datetimes aware end to end.",
)
def naive_utcnow(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = (ctx.qualname(call.func) or "").rsplit(".", 2)
        tail = ".".join(name[-2:]) if len(name) > 1 else name[-1]
        if tail in {"datetime.utcnow", "datetime.utcfromtimestamp"}:
            yield ctx.report(call, f"`{tail}()` returns a naive datetime (and is deprecated) — use `datetime.now(timezone.utc)`")


@check(
    id="bug/lru-cache-on-method",
    category="bugs",
    severity="medium",
    title="`lru_cache` on an instance method",
    why=(
        "The cache is stored on the function, so `self` becomes part of the key and every instance that was "
        "ever cached is kept alive forever — a steady memory leak, plus stale results when the instance changes."
    ),
    fix="Cache a module-level function of plain values, use `functools.cached_property`, or clear the cache per instance.",
)
def lru_cache_on_method(ctx: ModuleContext) -> Iterator[Finding]:
    for klass in ctx.nodes(ast.ClassDef):
        for func in [n for n in klass.body if isinstance(n, FUNCTION_TYPES)]:
            names = ctx.decorator_names(func)
            if not names & {"lru_cache", "cache", "functools.lru_cache", "functools.cache"}:
                continue
            first = func.args.args[0].arg if func.args.args else ""
            if first in {"self", "cls"} and "staticmethod" not in names:
                yield ctx.report(
                    func,
                    f"`{klass.name}.{func.name}` is cached with `self` in the key — instances are never released",
                    symbol=f"{klass.name}.{func.name}",
                )


@check(
    id="bug/unclosed-resource",
    category="bugs",
    severity="medium",
    title="File opened without a context manager",
    why=(
        "Without `with`, the file handle is released only when the garbage collector gets to it. On an "
        "exception the write is never flushed, and long-running processes run out of descriptors."
    ),
    fix="Use `with open(...) as f:` — or `contextlib.closing` for objects without context-manager support.",
)
def unclosed_resource(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        if (ctx.dotted(call.func) or "") not in {"open", "io.open", "codecs.open"}:
            continue
        if isinstance(ctx.parent(call), ast.withitem):
            continue
        scope = ctx.enclosing_function(call) or ctx.tree
        closes = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "close"
            for n in ast.walk(scope)
        )
        if closes:
            continue
        yield ctx.report(call, "`open()` without `with` — the file is not closed deterministically")


@check(
    id="bug/comparison-to-none",
    category="bugs",
    severity="low",
    title="`== None` instead of `is None`",
    why="Equality calls `__eq__`, which a custom class (or a numpy array / ORM field) can redefine, so the test may not mean what it says.",
    fix="Use `is None` / `is not None`.",
)
def comparison_to_none(ctx: ModuleContext) -> Iterator[Finding]:
    for compare in ctx.nodes(ast.Compare):
        for op, comparator in zip(compare.ops, compare.comparators):
            if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(comparator, ast.Constant) and comparator.value is None:
                yield ctx.report(compare, "compare to `None` with `is` / `is not`, not `==`")


@check(
    id="bug/type-equality",
    category="bugs",
    severity="low",
    title="Types compared with `==`",
    why="`type(x) == T` rejects subclasses, so legitimate values fail the check — a frequent cause of 'works with my class, not with theirs'.",
    fix="Use `isinstance(x, T)` (or `is` when you deliberately want the exact class).",
)
def type_equality(ctx: ModuleContext) -> Iterator[Finding]:
    for compare in ctx.nodes(ast.Compare):
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in compare.ops):
            continue
        sides = [compare.left, *compare.comparators]
        if any(isinstance(s, ast.Call) and (ctx.dotted(s.func) or "") == "type" for s in sides):
            yield ctx.report(compare, "`type(...) ==` ignores subclasses — use `isinstance(...)`")
