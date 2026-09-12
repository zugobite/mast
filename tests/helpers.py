"""Shared helpers: run the analyzer over a source string and ask what it found."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from mast.analysis import Settings, analyze_source


def isolate_mast_state(case, state: Path) -> None:
    """Point every module-level copy of ~/.mast at `state` for the life of `case`."""
    from mast import cli, common, learn

    state.mkdir(parents=True, exist_ok=True)
    patches = [
        mock.patch.object(common, "STATE", state),
        mock.patch.object(common, "REGISTRY", state / "registry.json"),
        mock.patch.object(common, "DECISIONS", state / "decisions.json"),
        mock.patch.object(learn, "STATE", state),
        mock.patch.object(learn, "DECISIONS", state / "decisions.json"),
        mock.patch.object(learn, "CENTRAL_CANDIDATES", state / "candidates.jsonl"),
        mock.patch.object(learn, "DISTILLED", state / "distilled.json"),
        mock.patch.object(learn, "REPORT", state / "learning-report.md"),
        mock.patch.object(cli, "REPORT", state / "learning-report.md"),
    ]
    for patch in patches:
        patch.start()
        case.addCleanup(patch.stop)


def findings(source: str, *, rel: str = "src/app/module.py", **settings_kwargs) -> list:
    return analyze_source(source, rel, Settings(**settings_kwargs)).findings


def check_ids(source: str, *, rel: str = "src/app/module.py", **settings_kwargs) -> list[str]:
    return [f.check for f in findings(source, rel=rel, **settings_kwargs)]


def assert_finds(case, source: str, check: str, *, rel: str = "src/app/module.py", **kwargs) -> None:
    ids = check_ids(source, rel=rel, **kwargs)
    case.assertIn(check, ids, f"expected {check} in {sorted(set(ids))}\n--- source ---\n{source}")


def assert_clean(case, source: str, check: str, *, rel: str = "src/app/module.py", **kwargs) -> None:
    ids = check_ids(source, rel=rel, **kwargs)
    case.assertNotIn(check, ids, f"unexpected {check}\n--- source ---\n{source}")
