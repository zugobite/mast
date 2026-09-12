#!/usr/bin/env python3
"""Shared helpers for mast's Claude Code hooks.

Copied into <project>/.claude/standards/hooks/ by `mast export` — edit them in the mast repo
and run `mast sync`. Standard library only, plus the analyzer vendored next to this file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

try:  # the analyzer is vendored beside the hooks by `mast export`
    import analysis as _analysis
    from analysis import Settings, analyze, analyze_source
    from analysis.context import matches_any
    from analysis.model import REGISTRY, SEVERITIES
    from analysis.render import render_compact
    from analysis.rules import active_rules as _active_rules, format_hits, invalid_rules, scan
except ImportError:  # pragma: no cover - export is broken; hooks must still run
    _analysis = None
    Settings = None  # type: ignore[assignment]
    REGISTRY = {}
    SEVERITIES = ("high", "medium", "low")

    def matches_any(rel: str, globs) -> bool:  # type: ignore[misc]
        return not globs

    def scan(rel: str, content: str, rules) -> list:  # type: ignore[misc]
        return []

    def format_hits(hits) -> str:  # type: ignore[misc]
        return ""

    def _active_rules(sources, config: dict | None = None) -> list:  # type: ignore[misc]
        return []

    def render_compact(findings, **kwargs) -> str:  # type: ignore[misc]
        return ""


PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
STD_DIR = PROJECT_DIR / ".claude" / "standards"
LEARN_DIR = STD_DIR / "learning"
STATE_DIR = STD_DIR / ".state"
CONFIG_PATH = PROJECT_DIR / ".claude" / "standards.config.json"
MAX_EVENT_BYTES = 5 * 1024 * 1024
PY_GLOBS = ["**/*.py", "**/*.pyi"]

COMMIT_DEFAULTS = {
    "enabled": True,
    "types": ["feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert"],
    "maxHeaderLength": 72,
    "protectedBranches": ["main", "master"],
    "checkpointLines": 150,
    "commitOnTurnEnd": True,
}


# ---------- io ----------


def read_stdin() -> str:
    try:
        return sys.stdin.read()
    except (OSError, ValueError):
        return ""


def read_input() -> dict:
    try:
        payload = json.loads(read_stdin() or "{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_json(path: str | Path, fallback: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def read_jsonl(path: str | Path) -> list:
    out = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a half-written line is skipped, not fatal
    return out


def load_config() -> dict:
    return read_json(CONFIG_PATH, {}) or {}


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload))


def add_context(event: str, context: str) -> None:
    emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}})


def tail(text: str, lines: int) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


# ---------- paths ----------


def to_rel(file: str | Path | None) -> str:
    if not file:
        return ""
    try:
        return os.path.relpath(str(Path(PROJECT_DIR, file).resolve()), str(PROJECT_DIR)).replace(os.sep, "/")
    except (OSError, ValueError):
        return ""


def is_inside(rel: str) -> bool:
    return bool(rel) and not rel.startswith("..") and not Path(rel).is_absolute()


def added_content(tool: str, tool_input: dict) -> str:
    """Only the text the tool is adding — pre-existing code is not re-flagged."""
    if tool == "Write":
        return tool_input.get("content") or ""
    if tool == "Edit":
        return tool_input.get("new_string") or ""
    if tool == "MultiEdit":
        return "\n".join(edit.get("new_string") or "" for edit in tool_input.get("edits") or [])
    if tool == "NotebookEdit":
        return tool_input.get("new_source") or ""
    return ""


def is_python(rel: str) -> bool:
    return rel.endswith((".py", ".pyi"))


# ---------- text rules ----------


def active_rules(config: dict | None = None) -> list[dict]:
    config = config if config is not None else load_config()
    return _active_rules(
        [
            read_json(STD_DIR / "rules.json", []) or [],
            read_json(STD_DIR / "local-rules.json", []) or [],
            config.get("rules") or [],
        ],
        config,
    )


# ---------- the analyzer ----------


def analysis_config(config: dict) -> dict:
    block = config.get("analysis")
    return block if isinstance(block, dict) else {}


def analysis_enabled(config: dict) -> bool:
    return _analysis is not None and analysis_config(config).get("enabled", True) is not False


def analysis_settings(config: dict) -> Any:
    return Settings.from_dict(analysis_config(config)) if _analysis is not None else None


def severities_at_or_above(levels: Iterable[str] | None) -> set:
    """Expand a config list like ["high"] into every severity at least that serious."""
    wanted = {str(level) for level in (levels or ["high"])}
    order = list(SEVERITIES)
    limit = max((order.index(level) for level in wanted if level in order), default=0)
    return set(order[: limit + 1])


def analyze_written_source(rel: str, content: str, config: dict) -> list:
    """Findings for a full file Claude is about to write (it has not hit disk yet)."""
    if not analysis_enabled(config) or not is_python(rel) or not content.strip():
        return []
    try:
        report = analyze_source(content, rel, analysis_settings(config))
    except Exception:  # mast: ignore[err/broad-except] a broken analyzer must never break a hook
        return []
    return report.findings


def analyze_project_files(rels: Iterable[str], config: dict) -> list:
    """Findings for files already on disk."""
    if not analysis_enabled(config):
        return []
    targets = [rel for rel in rels if is_python(rel) and (PROJECT_DIR / rel).exists()]
    if not targets:
        return []
    try:
        report = analyze(targets, analysis_settings(config), root=PROJECT_DIR)
    except Exception:  # mast: ignore[err/broad-except] a broken analyzer must never break a hook
        return []
    return report.findings


def blocking_findings(findings: Sequence[Any], config: dict, key: str = "blockOn") -> list:
    levels = severities_at_or_above(analysis_config(config).get(key) or ["high"])
    return [f for f in findings if f.severity in levels]


# ---------- events ----------


def log_event(event_type: str, **data) -> None:
    if os.environ.get("MAST_NO_LOG"):  # set by `mast doctor` self-tests
        return
    try:
        LEARN_DIR.mkdir(parents=True, exist_ok=True)
        file = LEARN_DIR / "events.jsonl"
        if file.exists() and file.stat().st_size > MAX_EVENT_BYTES:
            file.replace(LEARN_DIR / "events.1.jsonl")
        with file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "type": event_type, **data}) + "\n")
    except OSError:
        pass  # logging must never break a hook


def read_events(since_days: int = 30) -> list:
    cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400
    out = []
    for event in read_jsonl(LEARN_DIR / "events.jsonl"):
        try:
            when = datetime.fromisoformat(str(event.get("ts")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue  # an event without a usable timestamp is ignored
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when.timestamp() >= cutoff:
            out.append(event)
    return out


# ---------- per-session state ----------


def _state_path(session_id: str | None) -> Path:
    safe = re.sub(r"[^\w-]", "", str(session_id or "default"))
    return STATE_DIR / f"{safe}.json"


def load_state(session_id: str | None) -> dict:
    return {"edited": [], **(read_json(_state_path(session_id), {}) or {})}


def save_state(session_id: str | None, state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _state_path(session_id).write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass  # best effort: losing session state must not break the hook


# ---------- commands ----------


def shell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


def run_command(template: str, file: str | None, timeout_ms: int, changed: list[str] | None = None) -> dict:
    """`{file}` → one quoted path, `{changed}` → every changed path, quoted and space-separated."""
    command = template.replace("{file}", shell_quote(file) if file else "")
    command = command.replace("{changed}", " ".join(shell_quote(c) for c in (changed or [])))
    try:
        # The command is the project's own configured tool and every path is shell-quoted above; a shell
        # is required so configured pipelines work.
        result = subprocess.run(  # mast: ignore[sec/shell-injection]
            command,
            cwd=str(PROJECT_DIR),
            shell=True,
            capture_output=True,
            text=True,
            timeout=max(1, timeout_ms) / 1000,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {"status": 1, "timed_out": True, "output": f"{exc.stdout or ''}{exc.stderr or ''}"}
    except OSError as exc:
        return {"status": 1, "timed_out": False, "output": str(exc)}
    return {"status": result.returncode, "timed_out": False, "output": f"{result.stdout or ''}{result.stderr or ''}"}


# ---------- tests ----------


def related_test_files(changed: list[str], config: dict) -> list[str]:
    """Map changed source files to the test files that cover them (pytest has no --findRelatedTests)."""
    settings = analysis_settings(config)
    test_globs = list(settings.test_globs) if settings else ["**/test_*.py", "**/*_test.py", "tests/**"]
    tests: list[str] = []
    index: dict[str, list[str]] | None = None

    for rel in changed:
        if not is_python(rel):
            continue
        if matches_any(rel, test_globs):
            tests.append(rel)
            continue
        if index is None:
            index = {}
            for candidate in PROJECT_DIR.rglob("*.py"):
                name = candidate.name
                if not (name.startswith("test_") or name.endswith("_test.py")):
                    continue
                try:
                    rel_candidate = candidate.relative_to(PROJECT_DIR).as_posix()
                except ValueError:
                    continue  # a path outside the project is not a candidate test
                if any(part in {".venv", "venv", "node_modules", "__pycache__", ".tox"} for part in candidate.parts):
                    continue
                stem = name[5:-3] if name.startswith("test_") else name[:-8]
                index.setdefault(stem, []).append(rel_candidate)
        tests.extend(index.get(Path(rel).stem, []))
    return sorted(dict.fromkeys(tests))


# ---------- git ----------


def git(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=str(PROJECT_DIR), capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(args, 1, "", "")


def git_head() -> str | None:
    result = git(["rev-parse", "HEAD"])
    return result.stdout.strip() if result.returncode == 0 else None


def current_branch() -> str | None:
    result = git(["rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip() if result.returncode == 0 else None


def _length_of(rel: str) -> int:
    try:
        return len((PROJECT_DIR / rel).read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def diff_lines(files: list[str], base: str = "HEAD") -> int:
    """Lines changed in `files` since `base`, plus the full length of untracked files."""
    if not files:
        return 0
    diff = git(["diff", base or "HEAD", "--numstat", "--", *files])
    if diff.returncode != 0:
        return sum(_length_of(f) for f in files)
    total = 0
    for line in diff.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            total += _int(parts[0]) + _int(parts[1])
    untracked = [f for f in git(["ls-files", "--others", "--exclude-standard", "--", *files]).stdout.splitlines() if f]
    return total + sum(_length_of(f) for f in untracked)


def _int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def uncommitted_files(files: list[str]) -> list[str] | None:
    """Which of `files` have uncommitted changes; None outside a git work tree."""
    if not files:
        return []
    if git(["rev-parse", "--is-inside-work-tree"]).returncode != 0:
        return None
    tracked = git(["diff", "--name-only", "--relative", "HEAD", "--", *files])
    if tracked.returncode == 0:
        changed = [f for f in tracked.stdout.splitlines() if f]
    else:
        changed = [f for f in files if (PROJECT_DIR / f).exists()]
    untracked = [f for f in git(["ls-files", "--others", "--exclude-standard", "--", *files]).stdout.splitlines() if f]
    return sorted(dict.fromkeys([*changed, *untracked]))


def commit_settings(config: dict) -> dict:
    return {**COMMIT_DEFAULTS, **(config.get("commits") or {})}
