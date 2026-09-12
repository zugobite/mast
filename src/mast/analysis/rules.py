"""The regex rule engine: per-line text rules from packs, projects and config.

Rules catch what an AST pass cannot see — text in any file type, and the content of a
partial edit before it is a parseable module. AST checks catch everything else.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from .context import matches_any

FLAG_MAP = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}
SEVERITIES = ("block", "warn")


def read_json(path: str | Path, fallback: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def compile_flags(flags: str | None) -> int:
    value = 0
    for letter in str(flags or ""):
        value |= FLAG_MAP.get(letter, 0)
    return value


def compile_rule(rule: dict) -> re.Pattern | None:
    try:
        return re.compile(rule["pattern"], compile_flags(rule.get("flags")))
    except (re.error, KeyError, TypeError):
        return None


def active_rules(sources: Iterable[Sequence[dict]], config: dict | None = None) -> list[dict]:
    """Merge rule lists, then apply the project's `disabledRules` and `severityOverrides`."""
    config = config or {}
    disabled = set(config.get("disabledRules") or ())
    overrides = config.get("severityOverrides") or {}
    out: list[dict] = []
    for source in sources:
        for rule in source or ():
            if not isinstance(rule, dict) or not rule.get("id") or not rule.get("pattern"):
                continue
            if rule["id"] in disabled or overrides.get(rule["id"]) == "off":
                continue
            severity = overrides.get(rule["id"]) or rule.get("severity") or "warn"
            out.append({**rule, "severity": severity})
    return out


def invalid_rules(rules: Iterable[dict]) -> list[dict]:
    return [r for r in rules if compile_rule(r) is None]


def scan(rel: str, content: str, rules: Sequence[dict]) -> list[dict]:
    """Line numbers (1-based, within `content`) where each rule matches."""
    if not content:
        return []
    lines = content.splitlines()
    hits: list[dict] = []
    for rule in rules:
        if rule.get("globs") and not matches_any(rel, rule["globs"]):
            continue
        exclude = rule.get("exclude") or ()
        if exclude and matches_any(rel, exclude):
            continue
        pattern = compile_rule(rule)
        if pattern is None:
            continue
        at = [i + 1 for i, line in enumerate(lines) if pattern.search(line)]
        if at:
            hits.append({"rule": rule, "lines": at})
    return hits


def format_hits(hits: Sequence[dict]) -> str:
    out = []
    for hit in hits:
        lines = hit["lines"]
        where = ", ".join(str(n) for n in lines[:5]) + (", …" if len(lines) > 5 else "")
        out.append(f"- [{hit['rule']['id']}] {hit['rule'].get('message', '')} (line {where} of the new text)")
    return "\n".join(out)
