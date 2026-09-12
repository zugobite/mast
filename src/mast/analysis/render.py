"""Rendering findings: terminal, JSON, SARIF, and the compact form the hooks feed back."""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # avoids a circular import at runtime
    from . import Report

from .model import REGISTRY, SEVERITIES, Finding

_TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
_CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34", "cyan": "36", "magenta": "35"}
SEVERITY_COLOR = {"high": "red", "medium": "yellow", "low": "dim"}
SEVERITY_MARK = {"high": "✗", "medium": "!", "low": "·"}


def paint(text: str, color: str, *, tty: bool | None = None) -> str:
    use = _TTY if tty is None else tty
    code = _CODES.get(color)
    return f"\x1b[{code}m{text}\x1b[0m" if use and code else str(text)


def render_text(report: "Report", *, show_source: bool = True, explain: bool = False, tty: bool | None = None) -> str:
    """Findings grouped by file, in the order a reader would walk the code."""
    out: list[str] = []
    if not report.findings:
        out.append(paint(f"✓ No findings in {report.files} file(s).", "green", tty=tty))
        return _with_errors(out, report, tty)

    by_file: dict[str, list[Finding]] = {}
    for finding in report.findings:
        by_file.setdefault(finding.path, []).append(finding)

    for path, findings in by_file.items():
        out.append("")
        out.append(paint(path, "bold", tty=tty))
        for finding in findings:
            colour = SEVERITY_COLOR.get(finding.severity, "dim")
            mark = paint(SEVERITY_MARK.get(finding.severity, "·"), colour, tty=tty)
            head = f"  {mark} {finding.line}:{finding.col + 1}  {paint(finding.severity.ljust(6), colour, tty=tty)} {paint(finding.check, 'cyan', tty=tty)}"
            out.append(head)
            out.append(f"      {finding.message}")
            if show_source:
                snippet = _source_line(finding)
                if snippet:
                    out.append(paint(f"      │ {snippet}", "dim", tty=tty))
            if explain:
                meta = REGISTRY.get(finding.check)
                if meta:
                    out.append(paint(f"      why: {meta.why}", "dim", tty=tty))
                    out.append(paint(f"      fix: {meta.fix}", "dim", tty=tty))

    counts = report.counts()
    summary = " · ".join(
        paint(f"{counts[s]} {s}", SEVERITY_COLOR.get(s, "dim"), tty=tty) for s in SEVERITIES if counts.get(s)
    )
    out.append("")
    out.append(f"{len(report.findings)} finding(s) in {report.files} file(s): {summary or 'none'}")
    categories = report.by_category()
    if categories:
        out.append(paint("  " + " · ".join(f"{name} {count}" for name, count in categories.items()), "dim", tty=tty))
    out.append(paint("  `mast explain <check-id>` for the reasoning and the fix.", "dim", tty=tty))
    return _with_errors(out, report, tty)


def _with_errors(out: list[str], report: "Report", tty: bool | None) -> str:
    if report.skipped:
        out.append(paint(f"  {report.skipped} file(s) skipped (unreadable or too large).", "dim", tty=tty))
    for error in report.errors[:5]:
        out.append(paint(f"  ! {error}", "yellow", tty=tty))
    return "\n".join(out)


_SOURCE_CACHE: dict[str, list[str]] = {}


def _source_line(finding: Finding) -> str:
    lines = _SOURCE_CACHE.get(finding.path)
    if lines is None:
        try:
            with open(finding.path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            lines = []
        _SOURCE_CACHE[finding.path] = lines
    if 0 < finding.line <= len(lines):
        return lines[finding.line - 1].strip()[:120]
    return ""


def render_json(report: "Report") -> str:
    payload = {
        "files": report.files,
        "skipped": report.skipped,
        "checks": report.checks,
        "counts": report.counts(),
        "findings": [f.as_dict() for f in report.findings],
        "errors": report.errors,
    }
    return json.dumps(payload, indent=2) + "\n"


SARIF_LEVEL = {"high": "error", "medium": "warning", "low": "note"}


def render_sarif(report: "Report", *, version: str = "0.0.0") -> str:
    """SARIF 2.1.0 — consumable by GitHub code scanning and most CI dashboards."""
    used = sorted({f.check for f in report.findings})
    rules = []
    for check_id in used:
        meta = REGISTRY.get(check_id)
        if meta is None:
            continue
        rules.append(
            {
                "id": meta.id,
                "name": meta.id.replace("/", "."),
                "shortDescription": {"text": meta.title},
                "fullDescription": {"text": meta.why},
                "help": {"text": f"{meta.why}\n\nFix: {meta.fix}"},
                "defaultConfiguration": {"level": SARIF_LEVEL.get(meta.severity, "warning")},
                "properties": {"category": meta.category, "tags": [meta.category, *meta.tags]},
            }
        )
    results = [
        {
            "ruleId": f.check,
            "level": SARIF_LEVEL.get(f.severity, "warning"),
            "message": {"text": f.message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.path},
                        "region": {"startLine": f.line, "startColumn": f.col + 1, "endLine": f.end_line or f.line},
                    }
                }
            ],
        }
        for f in report.findings
    ]
    document = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "mast",
                        "version": version,
                        "informationUri": "https://github.com/zugobite/mast",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(document, indent=2) + "\n"


def render_compact(findings: Sequence[Finding], *, limit: int = 20, with_fix: bool = True) -> str:
    """One line per finding, for hook feedback — the text Claude reads and acts on."""
    out = []
    for finding in findings[:limit]:
        out.append(f"- {finding.path}:{finding.line} [{finding.severity}] {finding.check} — {finding.message}")
        if with_fix:
            meta = REGISTRY.get(finding.check)
            if meta and finding.severity == "high":
                out.append(f"    fix: {meta.fix}")
    if len(findings) > limit:
        out.append(f"- … and {len(findings) - limit} more — run `mast check` for the full list")
    return "\n".join(out)
