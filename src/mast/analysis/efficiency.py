"""Efficiency: code that is correct but does avoidable work, usually quadratically."""

from __future__ import annotations

import ast
from typing import Iterator

from .context import FUNCTION_TYPES, LOOP_TYPES, ModuleContext
from .model import Finding, check

LIST_BUILDERS = {"list", "sorted", "tuple", "split", "readlines", "fetchall", "values", "keys", "items"}
QUERY_CALLS = {"get", "filter", "all", "first", "count", "exists", "execute", "fetchone", "fetchall", "scalar", "query", "aggregate", "get_or_create", "select_related"}


def _list_like_names(ctx: ModuleContext) -> dict[tuple[int, str], int]:
    """Names bound to a list/tuple, keyed by (scope id, name) — membership tests on these are O(n).

    Scoped deliberately: a list called `names` in one function says nothing about a set called
    `names` in another.
    """
    found: dict[tuple[int, str], int] = {}
    for assign in ctx.nodes(ast.Assign, ast.AnnAssign):
        targets = assign.targets if isinstance(assign, ast.Assign) else [assign.target]
        value = assign.value
        if value is None:
            continue
        listish = isinstance(value, (ast.List, ast.Tuple, ast.ListComp))
        if isinstance(value, ast.Call):
            tail = (ctx.call_name(value) or "").rsplit(".", 1)[-1]
            listish = tail in LIST_BUILDERS
        if not listish:
            continue
        scope = id(ctx.enclosing_function(assign) or ctx.tree)
        for target in targets:
            if isinstance(target, ast.Name):
                found[(scope, target.id)] = assign.lineno
    return found


@check(
    id="perf/loop-string-concat",
    category="efficiency",
    severity="high",
    title="String built by repeated concatenation in a loop",
    why=(
        "Strings are immutable, so `s += part` copies the whole accumulated string on every iteration. The "
        "loop is quadratic: fine for ten rows, minutes of CPU for a large export."
    ),
    fix="Append the pieces to a list and `''.join(parts)` once, or write straight to a buffer/stream.",
)
def loop_string_concat(ctx: ModuleContext) -> Iterator[Finding]:
    for loop in ctx.nodes(*LOOP_TYPES):
        for node in ast.walk(loop):
            if not isinstance(node, ast.AugAssign) or not isinstance(node.op, ast.Add):
                continue
            if not isinstance(node.target, (ast.Name, ast.Attribute)):
                continue
            value = node.value
            is_text = isinstance(value, (ast.JoinedStr,)) or (isinstance(value, ast.Constant) and isinstance(value.value, str))
            if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mod):
                is_text = True
            if isinstance(value, ast.Call) and (ctx.call_name(value) or "").endswith(("format", "join", "str")):
                is_text = True
            if not is_text:
                continue
            yield ctx.report(
                node,
                f"`{ctx.dotted(node.target)} +=` inside a loop copies the whole string each iteration — collect the pieces and `''.join(...)` once",
            )


@check(
    id="perf/quadratic-membership",
    category="efficiency",
    severity="high",
    title="Membership test against a list inside a loop",
    why=(
        "`x in some_list` scans the list. Inside a loop that is O(n·m): a pair of 10k-row collections turns "
        "into 100 million comparisons, which is the difference between 20ms and half a minute."
    ),
    fix="Build a `set` (or dict) once before the loop and test membership against it.",
)
def quadratic_membership(ctx: ModuleContext) -> Iterator[Finding]:
    list_names = _list_like_names(ctx)
    if not list_names:
        return
    for compare in ctx.nodes(ast.Compare):
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in compare.ops):
            continue
        if ctx.enclosing_loop(compare, stop_at_function=False) is None:
            continue
        scope = id(ctx.enclosing_function(compare) or ctx.tree)
        for comparator in compare.comparators:
            if not isinstance(comparator, ast.Name) or comparator.id.isupper():
                continue  # a named constant is a fixed, small sequence
            declared = list_names.get((scope, comparator.id))
            if declared is None:
                continue
            yield ctx.report(
                compare,
                f"`in {comparator.id}` scans a list on every iteration — build a set from it (line {declared}) before the loop",
            )


@check(
    id="perf/list-membership-literal",
    category="efficiency",
    severity="low",
    title="Membership test against a list or tuple literal",
    why="A literal sequence is scanned element by element; a set literal hashes once. With several options the set is both faster and clearer about intent.",
    fix="Use a set literal: `if value in {\"a\", \"b\", \"c\"}:`.",
)
def list_membership_literal(ctx: ModuleContext) -> Iterator[Finding]:
    for compare in ctx.nodes(ast.Compare):
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in compare.ops):
            continue
        for comparator in compare.comparators:
            if not isinstance(comparator, (ast.List, ast.Tuple)) or len(comparator.elts) < 4:
                continue
            if all(isinstance(e, ast.Constant) and isinstance(e.value, (str, int, bool, float, bytes)) for e in comparator.elts):
                yield ctx.report(compare, f"membership test against a {len(comparator.elts)}-element literal — use a set literal `{{...}}`")


@check(
    id="perf/dict-keys-membership",
    category="efficiency",
    severity="low",
    title="`in d.keys()` instead of `in d`",
    why="`in d` hashes the key directly; going through `.keys()` builds a view first and reads as though the dict were a list.",
    fix="Drop `.keys()`: `if key in mapping:`.",
)
def dict_keys_membership(ctx: ModuleContext) -> Iterator[Finding]:
    for compare in ctx.nodes(ast.Compare):
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in compare.ops):
            continue
        for comparator in compare.comparators:
            if isinstance(comparator, ast.Call) and isinstance(comparator.func, ast.Attribute) and comparator.func.attr == "keys":
                yield ctx.report(compare, "`in d.keys()` — test `in d` directly")


STRONG_ORM_MARKERS = (".objects.", "session.query", "session.execute", "session.get", "cursor.execute", ".objects.get")
WEAK_ORM_MARKERS = ("query.", ".query", "cursor.", "db.", "conn.", ".execute", "session.")
DB_MODULES = ("django", "sqlalchemy", "sqlmodel", "psycopg2", "psycopg", "pymysql", "sqlite3", "asyncpg", "pymongo", "tortoise", "peewee", "models", "db", "databases")


@check(
    id="perf/n-plus-one-query",
    category="efficiency",
    severity="high",
    title="Database query inside a loop",
    why=(
        "One query per iteration is the N+1 problem: a page that looks instant with 10 rows issues hundreds "
        "of round trips in production. It is the single most common cause of slow enterprise endpoints."
    ),
    fix="Fetch the rows in one query before the loop (`filter(id__in=ids)`, a join, `select_related`/`selectinload`) and index the result in memory.",
)
def n_plus_one_query(ctx: ModuleContext) -> Iterator[Finding]:
    knows_a_database = ctx.imports_module(*DB_MODULES)
    for loop in ctx.nodes(*LOOP_TYPES):
        for node in ast.walk(loop):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in QUERY_CALLS:
                continue
            chain = ctx.dotted(node.func) or ""
            # A distinctive ORM chain speaks for itself; a vague one needs a database import nearby.
            strong = any(marker in chain for marker in STRONG_ORM_MARKERS)
            weak = knows_a_database and any(marker in chain for marker in WEAK_ORM_MARKERS)
            if not (strong or weak):
                continue
            yield ctx.report(
                node,
                f"`{chain}(...)` runs once per iteration (N+1 queries) — fetch the rows in a single query before the loop",
            )
            break


@check(
    id="perf/await-in-loop",
    category="efficiency",
    severity="low",
    title="Sequential `await` in a loop",
    why="Awaiting one call per iteration serialises work that could overlap, so total latency is the sum of every round trip instead of the slowest one.",
    fix="Collect the coroutines and `await asyncio.gather(*tasks)` — keep the loop only when each step depends on the previous result.",
)
def await_in_loop(ctx: ModuleContext) -> Iterator[Finding]:
    for loop in ctx.nodes(ast.For, ast.While):
        if ctx.in_async_function(loop) is None:
            continue
        awaits = [n for n in ast.walk(loop) if isinstance(n, ast.Await)]
        if len(awaits) != 1:
            continue
        yield ctx.report(awaits[0], "awaited once per iteration — if the calls are independent, gather them with `asyncio.gather`")


@check(
    id="perf/redundant-call",
    category="efficiency",
    severity="low",
    title="Redundant intermediate collection",
    why="Materialising a list only to hand it to another call allocates the whole sequence for nothing, and hides what the code actually wants.",
    fix="Drop the wrapper: `sorted(x)`, `len(x)`, `min(x)`, `next(iter(x))`, `sum(x for x in ...)`.",
)
def redundant_call(ctx: ModuleContext) -> Iterator[Finding]:
    lazy_consumers = {"any", "all", "sum", "min", "max", "sorted", "set", "tuple", "frozenset", "join"}
    for call in ctx.nodes(ast.Call):
        name = (ctx.dotted(call.func) or "").rsplit(".", 1)[-1]
        if name in {"sorted", "set", "tuple", "sum", "min", "max", "any", "all"} and len(call.args) == 1:
            inner = call.args[0]
            if isinstance(inner, ast.Call) and (ctx.dotted(inner.func) or "") == "list":
                yield ctx.report(call, f"`{name}(list(...))` — `{name}()` accepts any iterable, drop the `list()`")
        if name in lazy_consumers and len(call.args) == 1 and isinstance(call.args[0], ast.ListComp):
            yield ctx.report(call.args[0], f"`{name}([...])` builds the whole list first — pass a generator expression instead")
        if name == "list" and len(call.args) == 1 and isinstance(call.args[0], (ast.ListComp, ast.GeneratorExp)):
            comp = call.args[0]
            if isinstance(comp, ast.ListComp):
                yield ctx.report(call, "`list([...])` wraps a list comprehension that is already a list")
        if isinstance(ctx.parent(call), ast.Subscript) and name == "sorted":
            subscript = ctx.parent(call)
            index = getattr(subscript, "slice", None)
            if isinstance(index, ast.Constant) and index.value == 0:
                yield ctx.report(subscript, "`sorted(x)[0]` sorts the whole sequence to take one item — use `min(x)`")
        if name == "sum" and len(call.args) == 2 and isinstance(call.args[1], ast.List) and not call.args[1].elts:
            yield ctx.report(call, "`sum(lists, [])` concatenates quadratically — use `itertools.chain.from_iterable(lists)`")


@check(
    id="perf/identity-comprehension",
    category="efficiency",
    severity="low",
    title="Comprehension that copies without transforming",
    why="`[x for x in items]` walks the sequence in Python just to produce the same values a constructor would copy in C.",
    fix="Use `list(items)` / `set(items)` / `dict(mapping)`.",
)
def identity_comprehension(ctx: ModuleContext) -> Iterator[Finding]:
    for comp in ctx.nodes(ast.ListComp, ast.SetComp):
        if len(comp.generators) != 1:
            continue
        generator = comp.generators[0]
        if generator.ifs or not isinstance(generator.target, ast.Name) or not isinstance(comp.elt, ast.Name):
            continue
        if comp.elt.id == generator.target.id:
            kind = "list" if isinstance(comp, ast.ListComp) else "set"
            yield ctx.report(comp, f"comprehension copies without transforming — use `{kind}(...)`")


@check(
    id="perf/append-only-loop",
    category="efficiency",
    severity="low",
    title="Loop that only appends",
    why="A loop whose entire body is one `append` is a comprehension written the long way: more lines, a per-iteration method lookup, and less obvious intent.",
    fix="Use a comprehension: `result = [transform(x) for x in items if keep(x)]`.",
)
def append_only_loop(ctx: ModuleContext) -> Iterator[Finding]:
    for loop in ctx.nodes(ast.For):
        if loop.orelse:
            continue
        body = loop.body
        if len(body) == 1 and isinstance(body[0], ast.If) and not body[0].orelse:
            body = body[0].body
        if len(body) != 1 or not isinstance(body[0], ast.Expr):
            continue
        call = body[0].value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in {"append", "add"}:
            yield ctx.report(loop, f"loop body is a single `.{call.func.attr}()` — a comprehension expresses this directly")


@check(
    id="perf/range-len",
    category="efficiency",
    severity="low",
    title="`range(len(x))` index loop",
    why="Indexing through `range(len(...))` re-reads the sequence on every access and hides what is being iterated.",
    fix="Iterate the items (`for item in items:`) or use `enumerate(items)` when the index is needed.",
)
def range_len(ctx: ModuleContext) -> Iterator[Finding]:
    for loop in ctx.nodes(ast.For, ast.AsyncFor):
        call = loop.iter
        if not (isinstance(call, ast.Call) and (ctx.dotted(call.func) or "") == "range" and len(call.args) == 1):
            continue
        inner = call.args[0]
        if isinstance(inner, ast.Call) and (ctx.dotted(inner.func) or "") == "len":
            yield ctx.report(loop, "`for i in range(len(x))` — iterate `x` directly, or use `enumerate(x)`")


@check(
    id="perf/regex-compile-in-loop",
    category="efficiency",
    severity="medium",
    title="Regex compiled inside a loop",
    why="`re.compile` parses the pattern each time. In a loop or a hot function that cost is paid per row, for a value that never changes.",
    fix="Compile once at module level (`PATTERN = re.compile(...)`) and reuse it.",
)
def regex_compile_in_loop(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        if (ctx.qualname(call.func) or "") != "re.compile":
            continue
        if ctx.enclosing_loop(call, stop_at_function=False) is None:
            continue
        yield ctx.report(call, "`re.compile` inside a loop recompiles the same pattern — hoist it to module level")


@check(
    id="perf/deepcopy-in-loop",
    category="efficiency",
    severity="low",
    title="`deepcopy` inside a loop",
    why="A deep copy walks the entire object graph. Per iteration it dominates the loop, and it is usually defensive rather than needed.",
    fix="Copy once outside the loop, copy only the part you mutate, or build new immutable values instead.",
)
def deepcopy_in_loop(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        if (ctx.qualname(call.func) or "").endswith("copy.deepcopy") and ctx.enclosing_loop(call, stop_at_function=False):
            yield ctx.report(call, "`deepcopy` in a loop copies the whole object graph each iteration")


@check(
    id="perf/count-for-membership",
    category="efficiency",
    severity="low",
    title="`.count()` used as a membership test",
    why="`count()` always scans the whole sequence; `in` stops at the first match.",
    fix="Use `if value in items:`.",
)
def count_for_membership(ctx: ModuleContext) -> Iterator[Finding]:
    for compare in ctx.nodes(ast.Compare):
        left = compare.left
        if not (isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute) and left.func.attr == "count"):
            continue
        if len(compare.ops) == 1 and isinstance(compare.ops[0], (ast.Gt, ast.GtE, ast.Eq, ast.NotEq)):
            yield ctx.report(compare, "`.count(...)` scans everything just to test presence — use `in`")


@check(
    id="perf/pandas-row-loop",
    category="efficiency",
    severity="high",
    title="Row-by-row loop over a DataFrame",
    why=(
        "`iterrows`/`itertuples`/`apply(axis=1)` run Python per row and rebuild a Series each time — often "
        "100× slower than the vectorised equivalent, and the gap grows with the data."
    ),
    fix="Use vectorised column operations, `np.where`, `groupby`/`merge`, or `.to_numpy()` for the numeric core.",
)
def pandas_row_loop(ctx: ModuleContext) -> Iterator[Finding]:
    if not ctx.imports_module("pandas"):
        return
    for call in ctx.nodes(ast.Call):
        if not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr in {"iterrows", "itertuples"}:
            yield ctx.report(call, f"`.{call.func.attr}()` iterates row by row — vectorise the operation instead")
        elif call.func.attr == "apply":
            axis = ctx.keyword(call, "axis")
            if axis is not None and isinstance(axis.value, ast.Constant) and axis.value.value in (1, "columns"):
                yield ctx.report(call, "`.apply(..., axis=1)` runs Python per row — use vectorised column operations")


@check(
    id="perf/pandas-concat-in-loop",
    category="efficiency",
    severity="high",
    title="DataFrame grown inside a loop",
    why=(
        "`concat`/`append` in a loop reallocates and copies the whole frame on every iteration, making the "
        "build quadratic. (`DataFrame.append` was also removed in pandas 2.)"
    ),
    fix="Collect the pieces in a list and call `pd.concat(frames)` once after the loop.",
)
def pandas_concat_in_loop(ctx: ModuleContext) -> Iterator[Finding]:
    if not ctx.imports_module("pandas"):
        return
    for call in ctx.nodes(ast.Call):
        name = (ctx.qualname(call.func) or "")
        tail = name.rsplit(".", 1)[-1]
        if tail not in {"concat", "append"}:
            continue
        if tail == "append" and not isinstance(call.func, ast.Attribute):
            continue
        if ctx.enclosing_loop(call, stop_at_function=False) is None:
            continue
        yield ctx.report(call, f"`{tail}()` inside a loop copies the whole frame each iteration — collect the parts and concat once")
