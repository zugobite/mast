"""Finding a project's analyzer settings: mast config, pyproject, or sensible defaults."""

from __future__ import annotations

from pathlib import Path

from .analysis import Settings
from .common import read_json

MARKERS = (".claude/standards.config.json", ".mast.json", "pyproject.toml", "setup.cfg", ".git")


def find_project_root(start: str | Path = ".") -> Path:
    """Nearest ancestor that looks like a project root (config, pyproject or git)."""
    current = Path(start).resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        if any((directory / marker).exists() for marker in MARKERS):
            return directory
    return current


def _from_pyproject(file: Path) -> dict | None:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        return None
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tool = (data.get("tool") or {}).get("mast")
    if not isinstance(tool, dict):
        return None
    return tool.get("analysis") if isinstance(tool.get("analysis"), dict) else tool


def load_analysis_config(root: Path, explicit: str | Path | None = None) -> tuple[dict, str | None]:
    """The raw `analysis` settings block, plus where it came from."""
    if explicit:
        file = Path(explicit)
        data = read_json(file, None)
        if isinstance(data, dict):
            return (data.get("analysis") if isinstance(data.get("analysis"), dict) else data), str(file)
        return {}, str(file)

    config_file = root / ".claude" / "standards.config.json"
    config = read_json(config_file, None)
    if isinstance(config, dict) and isinstance(config.get("analysis"), dict):
        return config["analysis"], ".claude/standards.config.json"

    mast_json = read_json(root / ".mast.json", None)
    if isinstance(mast_json, dict):
        return (mast_json.get("analysis") if isinstance(mast_json.get("analysis"), dict) else mast_json), ".mast.json"

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        found = _from_pyproject(pyproject)
        if found is not None:
            return found, "pyproject.toml [tool.mast]"
    return {}, None


def load_settings(
    root: Path,
    *,
    config_file: str | Path | None = None,
    select: list[str] | None = None,
    ignore: list[str] | None = None,
) -> tuple[Settings, str | None]:
    raw, source = load_analysis_config(root, config_file)
    settings = Settings.from_dict(raw)
    if select:
        settings.select = tuple(select)
    if ignore:
        settings.ignore = tuple([*settings.ignore, *ignore])
    return settings, source
