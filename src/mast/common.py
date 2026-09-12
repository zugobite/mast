"""Paths, state, standards and profiles — the pieces every command shares."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import __version__
from .analysis.render import paint

PACKAGE_DIR = Path(__file__).resolve().parent
ANALYSIS_DIR = PACKAGE_DIR / "analysis"
STANDARD_FILE = "standard.json"


def _data_root() -> Path:
    """Where the shipped packs, profiles, hooks and templates live."""
    override = os.environ.get("MAST_HOME")
    if override:
        return Path(override).expanduser().resolve()
    # Installed from a clone (`pip install -e .`): the data dirs sit at the repo root.
    candidate = PACKAGE_DIR.parents[1]
    for parent in (candidate, *candidate.parents[:2]):
        if (parent / "standards").is_dir() and (parent / "hooks").is_dir():
            return parent
    return candidate


ROOT = _data_root()
STANDARDS = Path(os.environ.get("MAST_STANDARDS_DIR") or ROOT / "standards")
PROFILES = ROOT / "profiles"
HOOKS = ROOT / "hooks"
TEMPLATES = ROOT / "templates"

# Per-user state lives outside the install so upgrades never wipe it.
STATE = Path(os.environ.get("MAST_STATE_DIR") or Path.home() / ".mast")
REGISTRY = STATE / "registry.json"
DECISIONS = STATE / "decisions.json"


class MastError(Exception):
    """A problem worth showing the user without a traceback."""


def require_data_root() -> None:
    if not STANDARDS.is_dir() or not HOOKS.is_dir():
        raise MastError(
            f"mast's standards/hooks were not found at {tildify(ROOT)}. Install mast from a clone "
            "(`pip install -e .` in the mast repo) or point MAST_HOME at it. `mast check` works without them."
        )


class _Colors:
    """`c.red("text")` — colour helpers that no-op when stdout is not a terminal."""

    def __getattr__(self, name: str) -> Callable[[object], str]:
        return lambda text: paint(str(text), name)


c = _Colors()


# ---------- json / files ----------


def read_json(path: str | Path, fallback: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def read_json_strict(path: str | Path, fallback: Any = None) -> Any:
    """Raise on malformed JSON rather than silently replacing a user's file."""
    file = Path(path)
    if not file.exists():
        return fallback
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise MastError(f"Cannot parse {tildify(file)}: {exc}. Fix it and re-run.") from exc


def write_json(path: str | Path, data: Any) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: str | Path) -> list[dict]:
    out: list[dict] = []
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


def expand_home(path: str | Path) -> Path:
    return Path(str(path)).expanduser()


def tildify(path: str | Path) -> str:
    text = str(path)
    home = str(Path.home())
    return text.replace(home, "~") if text.startswith(home) else text


def is_dir(path: str | Path) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def load_registry() -> dict:
    data = read_json(REGISTRY, {}) or {}
    return {"projects": data.get("projects", {}), "standards": data.get("standards", {})}


def save_registry(registry: dict) -> None:
    write_json(REGISTRY, registry)


def git_output(args: Sequence[str], cwd: str | Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def version(root: str | Path = ROOT) -> str:
    """mast's version, with the short commit when running from a clone."""
    base = __version__
    if Path(root) != ROOT:
        pyproject = Path(root) / "pyproject.toml"
        package = read_json(Path(root) / "package.json", {}) or {}
        if package.get("version"):
            base = package["version"]
        elif pyproject.exists():
            for line in pyproject.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("version"):
                    base = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    sha = git_output(["rev-parse", "--short", "HEAD"], root)
    return f"{base}+{sha}" if sha else base


# ---------- standards: repos that layer packs, profiles and config on this core ----------


@dataclass
class Standard:
    name: str
    root: Path
    description: str = ""
    core: str = ""
    default_pack: str | None = None
    default_profile: str | None = None
    detect: list[dict] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    private_terms: list[str] = field(default_factory=list)

    @property
    def packs_dir(self) -> Path:
        return self.root / "packs"

    @property
    def profiles_dir(self) -> Path:
        return self.root / "profiles"

    @property
    def commands_dir(self) -> Path:
        return self.root / "commands"

    @property
    def agents_dir(self) -> Path:
        return self.root / "agents"


def load_standard(ref: str | Path | None) -> Standard | None:
    if not ref:
        return None
    registered = load_registry()["standards"].get(str(ref))
    root = expand_home(registered or ref).resolve()
    manifest = read_json(root / STANDARD_FILE, None)
    if not isinstance(manifest, dict) or not manifest.get("name"):
        raise MastError(
            f'No standard named "{ref}" is registered and {tildify(root)} has no valid {STANDARD_FILE}. '
            "Register one with `mast standard add <dir>`."
        )
    return Standard(
        name=manifest["name"],
        root=root,
        description=manifest.get("description", ""),
        core=manifest.get("core", ""),
        default_pack=manifest.get("defaultPack"),
        default_profile=manifest.get("defaultProfile"),
        detect=manifest.get("detect") or [],
        config=manifest.get("config") or {},
        private_terms=manifest.get("privateTerms") or [],
    )


def registered_standards() -> list[Standard]:
    out: list[Standard] = []
    for root in load_registry()["standards"].values():
        try:
            standard = load_standard(root)
        except MastError:
            continue  # a standard missing from disk is reported by `mast status`
        if standard:
            out.append(standard)
    return out


def pack_dir(pack: str, standard: Standard | None = None) -> Path:
    """A standard's pack shadows a core pack with the same name."""
    if standard and is_dir(standard.packs_dir / pack):
        return standard.packs_dir / pack
    if is_dir(STANDARDS / pack):
        return STANDARDS / pack
    where = f' (looked in standard "{standard.name}" and core)' if standard else ""
    raise MastError(f'Unknown pack "{pack}"{where}.')


@dataclass
class PackInfo:
    name: str
    origin: str
    dir: Path


def list_packs(standard: Standard | None = None) -> list[PackInfo]:
    def read(directory: Path, origin: str) -> list[PackInfo]:
        if not is_dir(directory):
            return []
        return [PackInfo(p.name, origin, p) for p in sorted(directory.iterdir()) if p.is_dir()]

    own = read(standard.packs_dir, standard.name) if standard else []
    names = {p.name for p in own}
    return [*own, *[p for p in read(STANDARDS, "core") if p.name not in names]]


def pack_markdown(pack: str, standard: Standard | None = None) -> list[str]:
    return sorted(p.name for p in pack_dir(pack, standard).glob("*.md"))


def pack_rules(pack: str, standard: Standard | None = None) -> list[dict]:
    return read_json(pack_dir(pack, standard) / "rules.json", []) or []


@dataclass
class Profile:
    name: str
    origin: str
    description: str = ""
    packs: list[str] = field(default_factory=list)
    code_paths: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)

    @classmethod
    def from_file(cls, file: Path, origin: str) -> "Profile":
        data = read_json(file, {}) or {}
        return cls(
            name=file.stem,
            origin=origin,
            description=data.get("description", ""),
            packs=data.get("packs") or [],
            code_paths=data.get("codePaths") or [],
            config=data.get("config") or {},
        )


def list_profiles(standard: Standard | None = None) -> list[Profile]:
    def read(directory: Path, origin: str) -> list[Profile]:
        if not is_dir(directory):
            return []
        return [Profile.from_file(f, origin) for f in sorted(directory.glob("*.json"))]

    own = read(standard.profiles_dir, standard.name) if standard else []
    names = {p.name for p in own}
    return [*own, *[p for p in read(PROFILES, "core") if p.name not in names]]


def load_profile(name: str, standard: Standard | None = None) -> Profile:
    profiles = list_profiles(standard)
    for profile in profiles:
        if profile.name == name:
            return profile
    available = ", ".join(p.name for p in profiles)
    raise MastError(f'Unknown profile "{name}". Available: {available}')


def source_hash(profile: Profile, standard: Standard | None = None) -> str:
    """Hash of everything an export of this profile would write — detects projects needing a sync."""
    digest = hashlib.sha1(usedforsecurity=False)

    def add_file(file: Path) -> None:
        digest.update(str(file.name).encode())
        try:
            digest.update(file.read_bytes())
        except OSError:
            pass  # an unreadable file cannot change the hash; drift is reported elsewhere

    def add_dir(directory: Path) -> None:
        if not is_dir(directory):
            return
        for entry in sorted(directory.rglob("*")):
            if entry.is_file() and "__pycache__" not in entry.parts:
                add_file(entry)

    digest.update(json.dumps(profile.packs).encode())
    for pack in profile.packs:
        add_dir(pack_dir(pack, standard))
    add_dir(HOOKS)
    add_dir(ANALYSIS_DIR)
    add_dir(TEMPLATES / "commands")
    add_dir(TEMPLATES / "agents")
    if standard:
        add_file(standard.root / STANDARD_FILE)
        add_dir(standard.commands_dir)
        add_dir(standard.agents_dir)
    return digest.hexdigest()[:12]


def pad(text: str, width: int) -> str:
    import re

    visible = re.sub(r"\x1b\[\d+m", "", str(text))
    return f"{text}{' ' * max(1, width - len(visible))}"


def which(binary: str) -> str | None:
    return shutil.which(binary)
