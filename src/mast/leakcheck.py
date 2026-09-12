"""Leak check: scan what git would publish for secrets, personal data and private terms."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .common import STANDARD_FILE, expand_home, read_json, registered_standards

MAX_BYTES = 1024 * 1024
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build", ".mypy_cache", ".ruff_cache"}

CHECKS = [
    (
        "secret",
        re.compile(
            r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|sk-(?:ant|live|proj)-[A-Za-z0-9_-]{20,}|"
            r"ghp_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
        ),
    ),
    ("credential", re.compile(r"\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\b\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']", re.IGNORECASE)),
    ("home-path", re.compile(r"/Users/[A-Za-z0-9._-]+/|/home/[a-z0-9._-]+/|[A-Z]:\\Users\\")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@(?!example\.(?:com|org|net)\b)(?!users\.noreply\.github\.com\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
]


@dataclass
class LeakReport:
    root: Path
    files: int = 0
    terms: list[str] = field(default_factory=list)
    hits: list[dict] = field(default_factory=list)


def _private_terms(root: Path, extra: list[str]) -> list[str]:
    own = (read_json(root / STANDARD_FILE, {}) or {}).get("name")
    terms = [
        term
        for standard in registered_standards()
        if standard.name != own
        for term in [standard.name, *standard.private_terms]
    ]
    return sorted({t for t in [*terms, *extra] if t and len(t) >= 4})


def _publishable_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return [f for f in result.stdout.split("\0") if f]
    except (OSError, subprocess.SubprocessError):
        pass  # not a git repo (or git is missing): fall back to walking the tree
    out: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and not SKIP_DIRS & set(path.relative_to(root).parts):
            out.append(path.relative_to(root).as_posix())
    return out


def leakcheck(target: str | Path, *, terms: list[str] | None = None) -> LeakReport:
    root = expand_home(target).resolve()
    private = _private_terms(root, terms or [])
    checks = list(CHECKS)
    if private:
        checks.append(("private-term", re.compile("|".join(re.escape(t) for t in private), re.IGNORECASE)))

    files = _publishable_files(root)
    report = LeakReport(root=root, files=len(files), terms=private)
    for rel in files:
        file = root / rel
        try:
            if file.stat().st_size > MAX_BYTES:
                continue
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable files cannot leak text
        if "\0" in text:
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            for name, pattern in checks:
                match = pattern.search(line)
                if match:
                    report.hits.append({"file": rel, "line": index, "check": name, "match": match.group(0)[:60]})
    return report
