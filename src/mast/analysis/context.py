"""Per-module analysis context: the parsed tree plus the lookups checks need."""

from __future__ import annotations

import ast
import fnmatch
import re
from dataclasses import dataclass, field
from typing import Iterator, Sequence

from .model import Finding

FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)
LOOP_TYPES = (ast.For, ast.AsyncFor, ast.While)
COMPREHENSION_TYPES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

DEFAULT_TEST_GLOBS = (
    "tests/**",
    "test/**",
    "**/tests/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/conftest.py",
)
DEFAULT_SCRIPT_GLOBS = (
    "scripts/**",
    "bin/**",
    "hooks/**",
    "**/hooks/*.py",
    "**/__main__.py",
    "**/cli.py",
    "**/cli/**",
    "**/main.py",
    "**/console.py",
    "manage.py",
    "noxfile.py",
    "tasks.py",
    "setup.py",
    "conftest.py",
)
DEFAULT_GENERATED_GLOBS = (
    "**/migrations/**",
    "**/*_pb2.py",
    "**/*_pb2_grpc.py",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/build/**",
    "**/dist/**",
    "**/.tox/**",
    "**/site-packages/**",
    "**/__pycache__/**",
)


@dataclass
class Settings:
    """Thresholds and switches for the analyzer (the `analysis` config block)."""

    max_function_lines: int = 50
    max_complexity: int = 10
    max_arguments: int = 6
    max_nesting: int = 4
    max_class_methods: int = 20
    max_class_lines: int = 400
    max_module_lines: int = 600
    max_locals: int = 15
    min_duplicate_statements: int = 6
    require_type_hints: bool = True
    require_docstrings: bool = False
    test_globs: tuple[str, ...] = DEFAULT_TEST_GLOBS
    script_globs: tuple[str, ...] = DEFAULT_SCRIPT_GLOBS
    exclude_globs: tuple[str, ...] = DEFAULT_GENERATED_GLOBS
    select: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()

    _KEYS = {
        "maxFunctionLines": "max_function_lines",
        "maxComplexity": "max_complexity",
        "maxArguments": "max_arguments",
        "maxNesting": "max_nesting",
        "maxClassMethods": "max_class_methods",
        "maxClassLines": "max_class_lines",
        "maxModuleLines": "max_module_lines",
        "maxLocals": "max_locals",
        "minDuplicateStatements": "min_duplicate_statements",
        "requireTypeHints": "require_type_hints",
        "requireDocstrings": "require_docstrings",
        "testGlobs": "test_globs",
        "scriptGlobs": "script_globs",
        "excludeGlobs": "exclude_globs",
        "select": "select",
        "ignore": "ignore",
    }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Settings":
        """Build settings from a config block; snake_case and camelCase both work."""
        settings = cls()
        for key, value in (data or {}).items():
            attr = cls._KEYS.get(key, key)
            if not hasattr(settings, attr) or attr.startswith("_"):
                continue
            current = getattr(settings, attr)
            if isinstance(current, tuple):
                value = tuple(value or ())
            elif isinstance(current, bool):
                value = bool(value)
            elif isinstance(current, int):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue  # a non-numeric override keeps the default threshold
            setattr(settings, attr, value)
        return settings


def matches_any(rel: str, globs: Sequence[str]) -> bool:
    """gitignore-flavoured glob match: `**/` spans directories, bare names match at any depth."""
    for raw in globs or ():
        pattern = str(raw).strip()
        if not pattern:
            continue
        if "/" not in pattern:
            pattern = f"**/{pattern}"
        if fnmatch.fnmatch(rel, pattern):
            return True
        # `tests/**` should also match the directory's own entries one level down.
        if pattern.endswith("/**") and fnmatch.fnmatch(rel, pattern[:-3]):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(rel, pattern[3:]):
            return True
    return False


SUPPRESS_RE = re.compile(r"#\s*mast:\s*ignore(?:\[([^\]]*)\])?")


SECRET_NAME_RE = re.compile(
    r"(?:^|_)(password|passwd|pwd|secret|token|api_?key|access_?key|private_?key|credential|auth)s?(?:$|_)",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(
    r"^(|none|null|todo|changeme|change_me|xxx+|placeholder|example|dummy|test|fake|\*+|\.\.\.|<.*>|\{\{.*\}\}|\$\{.*\})$",
    re.IGNORECASE,
)


class ModuleContext:
    """Everything a check needs about one module."""

    def __init__(
        self,
        *,
        path: str,
        rel: str,
        source: str,
        tree: ast.Module,
        settings: Settings,
    ) -> None:
        self.path = path
        self.rel = rel
        self.source = source
        self.tree = tree
        self.settings = settings
        self.lines = source.splitlines()
        self.is_test = matches_any(rel, settings.test_globs)
        self.is_script = matches_any(rel, settings.script_globs)
        self.is_init = rel.endswith("__init__.py")

        self.suppressed = self._read_suppressions()
        self.all: list[ast.AST] = []
        self.parents: dict[ast.AST, ast.AST] = {}
        self.import_aliases: dict[str, str] = {}  # local name -> dotted module or symbol
        self.imported_modules: set[str] = set()
        self._type_cache: dict[tuple, list] = {}
        self._current: str | None = None
        self._build()

    def _read_suppressions(self) -> dict[int, set[str]]:
        """`# mast: ignore` / `# mast: ignore[check/id, …]` comments, by line number."""
        found: dict[int, set[str]] = {}
        for index, line in enumerate(self.lines, start=1):
            match = SUPPRESS_RE.search(line)
            if not match:
                continue
            ids = match.group(1)
            found[index] = {part.strip() for part in ids.split(",") if part.strip()} if ids else set()
        return found

    def is_suppressed(self, line: int, check: str) -> bool:
        """A finding is suppressed by a comment on its own line, or on the line above it."""
        for candidate in (line, line - 1):
            if candidate in self.suppressed:
                allowed = self.suppressed[candidate]
                if not allowed or check in allowed or any(check.startswith(a.rstrip("*/")) for a in allowed):
                    return True
        return False

    # ---------- construction ----------

    def _build(self) -> None:
        for node in ast.walk(self.tree):
            self.all.append(node)
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.import_aliases[alias.asname or alias.name.split(".")[0]] = alias.name
                    self.imported_modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.imported_modules.add(node.module.split(".")[0])
                for alias in node.names:
                    self.import_aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    # ---------- traversal ----------

    def nodes(self, *types: type) -> list:
        """Every node of the given AST types, in document order."""
        key = tuple(sorted(t.__name__ for t in types))
        cached = self._type_cache.get(key)
        if cached is None:
            cached = [n for n in self.all if isinstance(n, types)]
            self._type_cache[key] = cached
        return cached

    def functions(self) -> list:
        return self.nodes(*FUNCTION_TYPES)

    def parent(self, node: ast.AST) -> ast.AST | None:
        return self.parents.get(node)

    def ancestors(self, node: ast.AST) -> Iterator[ast.AST]:
        current = self.parents.get(node)
        while current is not None:
            yield current
            current = self.parents.get(current)

    def enclosing(self, node: ast.AST, *types: type) -> ast.AST | None:
        for parent in self.ancestors(node):
            if isinstance(parent, types):
                return parent
        return None

    def enclosing_function(self, node: ast.AST) -> ast.AST | None:
        return self.enclosing(node, *FUNCTION_TYPES)

    def enclosing_loop(self, node: ast.AST, stop_at_function: bool = True) -> ast.AST | None:
        for parent in self.ancestors(node):
            if isinstance(parent, LOOP_TYPES):
                return parent
            if stop_at_function and isinstance(parent, FUNCTION_TYPES):
                return None
        return None

    def enclosing_class(self, node: ast.AST) -> ast.ClassDef | None:
        result = self.enclosing(node, ast.ClassDef)
        return result if isinstance(result, ast.ClassDef) else None

    def in_async_function(self, node: ast.AST) -> ast.AsyncFunctionDef | None:
        for parent in self.ancestors(node):
            if isinstance(parent, ast.AsyncFunctionDef):
                return parent
            if isinstance(parent, ast.FunctionDef):
                return None
        return None

    def is_module_level(self, node: ast.AST) -> bool:
        return not any(isinstance(p, FUNCTION_TYPES + (ast.ClassDef,)) for p in self.ancestors(node))

    # ---------- names ----------

    def dotted(self, node: ast.AST | None) -> str | None:
        """`a.b.c` for Name/Attribute chains, else None."""
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
            return ".".join(reversed(parts))
        if isinstance(node, ast.Call):
            return None
        return None

    def qualname(self, node: ast.AST | None) -> str | None:
        """Dotted name with imports resolved: `np.array` -> `numpy.array`."""
        dotted = self.dotted(node)
        if not dotted:
            return None
        head, _, rest = dotted.partition(".")
        target = self.import_aliases.get(head)
        if not target:
            return dotted
        return f"{target}.{rest}" if rest else target

    def call_name(self, call: ast.Call) -> str | None:
        return self.qualname(call.func) if isinstance(call, ast.Call) else None

    def calls_named(self, *names: str) -> Iterator[ast.Call]:
        """Calls whose resolved name matches any of `names` (also matches bare attribute tails)."""
        wanted = set(names)
        tails = {n.rsplit(".", 1)[-1] for n in names}
        for call in self.nodes(ast.Call):
            resolved = self.call_name(call)
            if resolved and (resolved in wanted or resolved.rsplit(".", 1)[-1] in tails and self._tail_ok(resolved, wanted)):
                yield call

    @staticmethod
    def _tail_ok(resolved: str, wanted: set[str]) -> bool:
        return any(resolved == w or resolved.endswith("." + w) for w in wanted)

    def keyword(self, call: ast.Call, name: str) -> ast.keyword | None:
        for kw in call.keywords:
            if kw.arg == name:
                return kw
        return None

    def uses(self, node: ast.AST, name: str) -> bool:
        return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))

    def imports_module(self, *modules: str) -> bool:
        return any(m in self.imported_modules for m in modules)

    # ---------- source ----------

    def segment(self, node: ast.AST) -> str:
        try:
            return ast.get_source_segment(self.source, node) or ""
        except Exception:  # pragma: no cover - defensive
            return ""

    def line_text(self, line: int) -> str:
        return self.lines[line - 1] if 0 < line <= len(self.lines) else ""

    @staticmethod
    def docstring(node: ast.AST) -> str | None:
        if isinstance(node, (ast.Module, ast.ClassDef) + FUNCTION_TYPES):
            return ast.get_docstring(node)
        return None

    @staticmethod
    def is_public(name: str) -> bool:
        return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))

    def decorator_names(self, node: ast.AST) -> set[str]:
        names: set[str] = set()
        for dec in getattr(node, "decorator_list", []):
            target = dec.func if isinstance(dec, ast.Call) else dec
            for value in (self.dotted(target), self.qualname(target)):
                if value:
                    names.add(value)
                    names.add(value.rsplit(".", 1)[-1])
        return names

    # ---------- reporting ----------

    def report(
        self,
        node: ast.AST,
        message: str,
        *,
        severity: str | None = None,
        symbol: str | None = None,
        check: str | None = None,
    ) -> Finding:
        from .model import REGISTRY

        check_id = check or self._current or "?"
        meta = REGISTRY.get(check_id)
        return Finding(
            check=check_id,
            category=meta.category if meta else "bugs",
            severity=severity or (meta.severity if meta else "medium"),
            message=message,
            path=self.rel,
            line=getattr(node, "lineno", 1),
            col=getattr(node, "col_offset", 0),
            end_line=getattr(node, "end_lineno", None),
            symbol=symbol,
        )


# ---------- shared analysis helpers ----------

MUTABLE_CALLS = {"list", "dict", "set", "bytearray", "collections.defaultdict", "collections.OrderedDict", "collections.Counter", "collections.deque"}


def is_mutable_literal(node: ast.AST | None, ctx: ModuleContext | None = None) -> bool:
    """True for values that are a fresh mutable container (unsafe as a shared default)."""
    if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if isinstance(node, ast.Call) and ctx is not None:
        name = ctx.call_name(node)
        return bool(name and (name in MUTABLE_CALLS or name.rsplit(".", 1)[-1] in {"list", "dict", "set", "defaultdict", "Counter", "deque", "OrderedDict"}))
    return False


def statement_count(node: ast.AST) -> int:
    return sum(1 for child in ast.walk(node) if isinstance(child, ast.stmt))


def body_lines(node: ast.AST) -> int:
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", start) or start
    return max(1, end - start + 1)


def complexity(node: ast.AST) -> int:
    """Cyclomatic complexity: one path plus every branch, loop, guard and handler."""
    score = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.IfExp) + LOOP_TYPES):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.ExceptHandler):
            score += 1
        elif isinstance(child, ast.comprehension):
            score += 1 + len(child.ifs)
        elif isinstance(child, ast.Assert):
            score += 1
        elif isinstance(child, getattr(ast, "Match", ast.AST)) and hasattr(child, "cases"):
            score += len(child.cases)
    return score


def nesting_depth(node: ast.AST) -> tuple[int, ast.AST | None]:
    """Deepest block nesting inside a function, and the node that reaches it."""
    deepest, culprit = 0, None

    def walk(n: ast.AST, depth: int) -> None:
        nonlocal deepest, culprit
        for child in ast.iter_child_nodes(n):
            step = isinstance(child, (ast.If, ast.With, ast.AsyncWith, ast.Try, ast.ExceptHandler) + LOOP_TYPES)
            new_depth = depth + (1 if step else 0)
            if step and new_depth > deepest:
                deepest, culprit = new_depth, child
            walk(child, new_depth)

    walk(node, 0)
    return deepest, culprit


def assigned_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for target in ast.walk(node):
        if isinstance(target, ast.Name) and isinstance(target.ctx, (ast.Store, ast.Del)):
            names.add(target.id)
    return names


def returns_or_raises(body: Sequence[ast.stmt]) -> bool:
    return any(isinstance(stmt, (ast.Return, ast.Raise)) for stmt in body)
