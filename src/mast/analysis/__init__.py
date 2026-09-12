"""mast's Python analyzer: a bug hunter, efficiency and structure reviewer built on `ast`.

Self-contained and dependency-free so `mast export` can vendor it into a project's
`.claude/standards/hooks/analysis/`, where the hooks run it with plain `python3`.

    from mast.analysis import analyze, Settings
    report = analyze(["src"], Settings())
    for finding in report.findings:
        print(finding.location, finding.check, finding.message)
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import bugs, efficiency, security, standards, structure  # noqa: F401  (registers the checks)
from .context import ModuleContext, Settings, matches_any
from .model import (
    CATEGORIES,
    REGISTRY,
    SEVERITIES,
    SEVERITY_ORDER,
    Check,
    Finding,
    all_checks,
    findings_to_json,
    resolve_selection,
)

__all__ = [
    "CATEGORIES",
    "Check",
    "Finding",
    "Report",
    "SEVERITIES",
    "SEVERITY_ORDER",
    "Settings",
    "active_checks",
    "all_checks",
    "analyze",
    "analyze_source",
    "collect_files",
    "findings_to_json",
    "resolve_selection",
]

MAX_BYTES = 2 * 1024 * 1024


@dataclass
class Report:
    """The outcome of one analysis run."""

    findings: list[Finding] = field(default_factory=list)
    files: int = 0
    skipped: int = 0
    checks: int = 0
    errors: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {severity: 0 for severity in SEVERITIES}
        for finding in self.findings:
            out[finding.severity] = out.get(finding.severity, 0) + 1
        return out

    def by_category(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for finding in self.findings:
            out[finding.category] = out.get(finding.category, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def worst(self) -> str | None:
        severities = {f.severity for f in self.findings}
        for severity in SEVERITIES:
            if severity in severities:
                return severity
        return None

    def exceeds(self, threshold: str | None) -> bool:
        """True when any finding is at or above `threshold` ("high"/"medium"/"low"; None = never)."""
        if not threshold or threshold == "none":
            return False
        limit = SEVERITY_ORDER.get(threshold, 0)
        return any(SEVERITY_ORDER[f.severity] <= limit for f in self.findings)


def active_checks(settings: Settings) -> list[Check]:
    """The checks that should run, after `select` and `ignore`."""
    selected = resolve_selection(settings.select) if settings.select else set(REGISTRY)
    ignored = resolve_selection(settings.ignore) if settings.ignore else set()
    ids = (selected - ignored) - {"bug/syntax-error"}
    return [REGISTRY[i] for i in sorted(ids)]


def collect_files(paths: Sequence[str | os.PathLike], settings: Settings, root: Path | None = None) -> list[Path]:
    """Every `.py` file under `paths`, minus generated and vendored trees."""
    base = Path(root or Path.cwd())
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in paths or ["."]:
        target = Path(raw)
        if not target.is_absolute():
            target = (base / target) if not target.exists() else target
        if target.is_file():
            candidates = [target]
        else:
            candidates = sorted(target.rglob("*.py"))
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen or candidate.suffix != ".py":
                continue
            seen.add(resolved)
            if matches_any(_relative(candidate, base), settings.exclude_globs):
                continue
            found.append(candidate)
    return found


def _relative(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def analyze_source(
    source: str,
    rel: str,
    settings: Settings | None = None,
    *,
    path: str | None = None,
) -> Report:
    """Analyze a single module held in memory — used by the write-time hook."""
    settings = settings or Settings()
    report = Report(checks=len(active_checks(settings)))
    context = _context_for(source, rel, path or rel, settings, report)
    if context is not None:
        _run_module_checks([context], settings, report)
        _run_project_checks([context], settings, report)
    report.files = 1
    report.findings.sort(key=Finding.sort_key)
    return report


def analyze(
    paths: Sequence[str | os.PathLike],
    settings: Settings | None = None,
    *,
    root: str | os.PathLike | None = None,
    changed_lines: dict[str, set[int]] | None = None,
) -> Report:
    """Analyze every Python file under `paths`."""
    settings = settings or Settings()
    base = Path(root or Path.cwd())
    checks = active_checks(settings)
    report = Report(checks=len(checks))
    contexts: list[ModuleContext] = []

    for file in collect_files(paths, settings, base):
        rel = _relative(file, base)
        try:
            if file.stat().st_size > MAX_BYTES:
                report.skipped += 1
                continue
            source = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report.skipped += 1
            report.errors.append(f"{rel}: {exc}")
            continue
        report.files += 1
        context = _context_for(source, rel, str(file), settings, report)
        if context is not None:
            contexts.append(context)

    _run_module_checks(contexts, settings, report)
    _run_project_checks(contexts, settings, report)

    if changed_lines is not None:
        report.findings = [f for f in report.findings if f.line in changed_lines.get(f.path, set())]
    report.findings.sort(key=Finding.sort_key)
    return report


def _context_for(source: str, rel: str, path: str, settings: Settings, report: Report) -> ModuleContext | None:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        report.findings.append(
            Finding(
                check="bug/syntax-error",
                category="bugs",
                severity="high",
                message=f"{exc.msg} — this file does not parse, so nothing else could be checked",
                path=rel,
                line=exc.lineno or 1,
                col=max(0, (exc.offset or 1) - 1),
            )
        )
        return None
    except (ValueError, RecursionError) as exc:  # pragma: no cover - pathological input
        report.errors.append(f"{rel}: {exc}")
        return None
    return ModuleContext(path=path, rel=rel, source=source, tree=tree, settings=settings)


def _run_module_checks(contexts: Iterable[ModuleContext], settings: Settings, report: Report) -> None:
    checks = [c for c in active_checks(settings) if not c.project_wide]
    for context in contexts:
        for check in checks:
            context._current = check.id
            try:
                report.findings.extend(
                    f for f in check.fn(context) if not context.is_suppressed(f.line, f.check)
                )
            except Exception as exc:  # a broken check must never fail the run
                report.errors.append(f"{context.rel}: check {check.id} crashed: {type(exc).__name__}: {exc}")
            finally:
                context._current = None


def _run_project_checks(contexts: Sequence[ModuleContext], settings: Settings, report: Report) -> None:
    by_path = {context.rel: context for context in contexts}
    for check in [c for c in active_checks(settings) if c.project_wide]:
        try:
            report.findings.extend(
                f
                for f in check.fn(contexts)
                if f.path not in by_path or not by_path[f.path].is_suppressed(f.line, f.check)
            )
        except Exception as exc:  # pragma: no cover - defensive
            report.errors.append(f"check {check.id} crashed: {type(exc).__name__}: {exc}")
