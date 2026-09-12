"""Findings, check metadata and the check registry.

This package is self-contained on purpose: `mast export` vendors it into each
project's `.claude/standards/hooks/analysis/`, so the hooks can run the analyzer
with nothing installed but Python itself. Import only the standard library here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

SEVERITIES = ("high", "medium", "low")
SEVERITY_ORDER = {s: i for i, s in enumerate(SEVERITIES)}
CATEGORIES = ("bugs", "efficiency", "structure", "standards", "security")


@dataclass(frozen=True)
class Finding:
    """One problem found at one place."""

    check: str
    category: str
    severity: str
    message: str
    path: str
    line: int
    col: int = 0
    end_line: int | None = None
    symbol: str | None = None

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}:{self.col + 1}"

    def as_dict(self) -> dict:
        return {
            "check": self.check,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.col + 1,
            "endLine": self.end_line or self.line,
            "symbol": self.symbol,
        }

    def sort_key(self) -> tuple:
        return (self.path, self.line, self.col, self.check)


@dataclass(frozen=True)
class Check:
    """A single analysis rule: metadata plus the function that runs it."""

    id: str
    category: str
    severity: str
    title: str
    why: str
    fix: str
    fn: Callable[..., Iterator[Finding]]
    project_wide: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "why": self.why,
            "fix": self.fix,
            "projectWide": self.project_wide,
            "tags": list(self.tags),
        }


REGISTRY: dict[str, Check] = {}


def check(
    *,
    id: str,
    category: str,
    severity: str,
    title: str,
    why: str,
    fix: str,
    project_wide: bool = False,
    tags: Iterable[str] = (),
) -> Callable:
    """Register an analysis check. `fn(ctx)` yields findings via `ctx.report(...)`."""

    if category not in CATEGORIES:
        raise ValueError(f"{id}: unknown category {category!r}")
    if severity not in SEVERITIES:
        raise ValueError(f"{id}: unknown severity {severity!r}")

    def wrap(fn: Callable) -> Callable:
        if id in REGISTRY:
            raise ValueError(f"duplicate check id {id!r}")
        REGISTRY[id] = Check(
            id=id,
            category=category,
            severity=severity,
            title=title,
            why=why.strip(),
            fix=fix.strip(),
            fn=fn,
            project_wide=project_wide,
            tags=tuple(tags),
        )
        return fn

    return wrap


def all_checks() -> list[Check]:
    return sorted(REGISTRY.values(), key=lambda c: (c.category, c.id))


def resolve_selection(ids: Iterable[str]) -> set[str]:
    """Expand ids, category names and `prefix/` patterns into concrete check ids."""
    wanted: set[str] = set()
    for raw in ids:
        token = str(raw).strip()
        if not token:
            continue
        if token in REGISTRY:
            wanted.add(token)
            continue
        if token in CATEGORIES:
            wanted.update(c.id for c in REGISTRY.values() if c.category == token)
            continue
        prefix = token.rstrip("*").rstrip("/")
        matched = {c.id for c in REGISTRY.values() if c.id.startswith(prefix)}
        if not matched:
            raise ValueError(f"unknown check, category or prefix: {token!r}")
        wanted.update(matched)
    return wanted


def findings_to_json(findings: list[Finding], *, indent: int = 2) -> str:
    return json.dumps([f.as_dict() for f in findings], indent=indent) + "\n"
