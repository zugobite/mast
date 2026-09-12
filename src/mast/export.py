"""Exporting a project: standards, rules, hooks, the analyzer, commands and the CLAUDE.md block."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .analysis.context import DEFAULT_SCRIPT_GLOBS, DEFAULT_TEST_GLOBS
from .common import (
    ANALYSIS_DIR,
    HOOKS,
    TEMPLATES,
    MastError,
    Profile,
    Standard,
    expand_home,
    is_dir,
    list_profiles,
    load_profile,
    load_registry,
    load_standard,
    pack_dir,
    pack_markdown,
    pack_rules,
    read_json,
    read_json_strict,
    registered_standards,
    require_data_root,
    save_registry,
    source_hash,
    tildify,
    version,
    write_json,
)

BLOCK_RE = re.compile(r"<!-- mast:start[\s\S]*?<!-- mast:end -->\n?")
HOOK_MARK = ".claude/standards/hooks/"
MANAGED_MARK = "managed by mast"
PY_GLOBS = ["**/*.py", "**/*.pyi"]
GITIGNORE_BLOCK = "# mast (local learning data)\n.claude/standards/learning/\n.claude/standards/.state/\n"

COMMIT_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "types": ["feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert"],
    "maxHeaderLength": 72,
    "protectedBranches": ["main", "master"],
    "checkpointLines": 150,
    "commitOnTurnEnd": True,
}

ANALYSIS_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "blockOn": ["high"],
    "gateOn": ["high"],
    "select": [],
    "ignore": [],
    "maxFunctionLines": 50,
    "maxComplexity": 10,
    "maxArguments": 6,
    "maxNesting": 4,
    "maxClassMethods": 20,
    "maxClassLines": 400,
    "maxModuleLines": 600,
    "maxLocals": 15,
    "minDuplicateStatements": 6,
    "requireTypeHints": True,
    "requireDocstrings": False,
    "testGlobs": list(DEFAULT_TEST_GLOBS),
    "scriptGlobs": list(DEFAULT_SCRIPT_GLOBS),
}

LOCAL_LESSONS = """# Project grievances & overrides

Mistakes not to repeat in this repo, plus project-specific overrides of the shared standards.
Owned by this repo — `mast sync` never overwrites it. `mast promote <id> --scope project` appends grievances here.

<!-- mast:grievances -->
"""

LOCAL_WINS = """# Project wins — reproduce these

Approaches the user loved in this repo. Reuse them for similar work and read the listed exemplar files first.
Owned by this repo — `mast sync` never overwrites it. `mast promote <id> --scope project` appends wins here.

<!-- mast:wins -->
"""

# ---------- detection ----------

WEB_FRAMEWORKS = {"flask", "starlette", "aiohttp", "sanic", "tornado", "bottle", "litestar", "falcon", "quart", "pyramid"}
DATA_LIBS = {"pandas", "numpy", "polars", "scikit-learn", "sklearn", "scipy", "pyspark", "dask", "apache-airflow", "dagster", "prefect", "dbt-core", "duckdb", "torch", "tensorflow", "transformers"}
NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalise(name: str) -> str:
    """`Flask-SQLAlchemy>=3` -> `flask-sqlalchemy`."""
    match = NAME_RE.match(name.strip().lower().replace("_", "-"))
    return match.group(1) if match else ""


def read_dependencies(directory: str | Path) -> set[str]:
    """Every declared dependency name we can find, normalised to lowercase dashes."""
    root = Path(directory)
    found: set[str] = set()

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        data: dict = {}
        try:
            import tomllib

            data = tomllib.loads(text)
        except Exception:
            data = {}
        if data:
            project = data.get("project") or {}
            declared: list[str] = list(project.get("dependencies") or [])
            for extra in (project.get("optional-dependencies") or {}).values():
                declared += list(extra or [])
            for group in (data.get("dependency-groups") or {}).values():
                declared += [g for g in (group or []) if isinstance(g, str)]
            poetry = ((data.get("tool") or {}).get("poetry") or {})
            declared += list((poetry.get("dependencies") or {}).keys())
            for group in (poetry.get("group") or {}).values():
                declared += list((group.get("dependencies") or {}).keys())
            found.update(_normalise(d) for d in declared)
        else:
            # No tomllib (or invalid TOML): fall back to scanning dependency-looking lines.
            for line in text.splitlines():
                match = re.match(r'\s*["\']([A-Za-z0-9][A-Za-z0-9._-]+)\s*(?:[<>=!~\[;].*)?["\']\s*,?\s*$', line)
                if match:
                    found.add(_normalise(match.group(1)))

    for requirements in list(root.glob("requirements*.txt")) + list(root.glob("requirements/*.txt")):
        for line in requirements.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                found.add(_normalise(line))

    pipfile = root / "Pipfile"
    if pipfile.exists():
        for line in pipfile.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r'\s*"?([A-Za-z0-9][A-Za-z0-9._-]+)"?\s*=', line)
            if match:
                found.add(_normalise(match.group(1)))

    setup_cfg = root / "setup.cfg"
    if setup_cfg.exists():
        for line in setup_cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"\s{4,}([A-Za-z0-9][A-Za-z0-9._-]+)", line)
            if match:
                found.add(_normalise(match.group(1)))

    found.discard("python")
    return {name for name in found if name}


def detect_profile(directory: str | Path) -> str:
    """Which stock profile a repo looks like."""
    root = Path(directory)
    deps = read_dependencies(root)
    has_any = lambda names: bool(deps & set(names))  # noqa: E731

    if "django" in deps or (root / "manage.py").exists():
        return "django"
    if "fastapi" in deps:
        return "fastapi"
    if has_any(WEB_FRAMEWORKS):
        return "web-service"
    if has_any(DATA_LIBS) or list(root.glob("notebooks/*.ipynb")) or list(root.glob("*.ipynb")):
        return "data"
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        text = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace") if (root / "pyproject.toml").exists() else ""
        if "[build-system]" in text or (root / "setup.py").exists():
            return "lib"
    return "base"


def matches_detect_rule(rule: dict, directory: Path, deps: set[str]) -> bool:
    if rule.get("name") and not re.search(rule["name"], directory.name, re.IGNORECASE):
        return False
    if rule.get("anyDependency") and not any(_normalise(d) in deps for d in rule["anyDependency"]):
        return False
    if rule.get("allDependencies") and not all(_normalise(d) in deps for d in rule["allDependencies"]):
        return False
    if rule.get("noDependency") and any(_normalise(d) in deps for d in rule["noDependency"]):
        return False
    if rule.get("hasFile") and not any((directory / f).exists() for f in rule["hasFile"]):
        return False
    return True


def detect_target(directory: Path, standard: Standard | None = None) -> tuple[Standard | None, str]:
    """First matching detect rule of any registered standard wins; else the core's own detection."""
    deps = read_dependencies(directory)
    for candidate in [standard] if standard else registered_standards():
        if candidate is None:
            continue
        for rule in candidate.detect:
            if matches_detect_rule(rule, directory, deps):
                return candidate, rule["profile"]
    if standard:
        return standard, standard.default_profile or detect_profile(directory)
    return None, detect_profile(directory)


def resolve_target(
    directory: Path,
    *,
    standard: str | Path | None | object = ...,
    profile: str | None = None,
) -> tuple[Standard | None, Profile]:
    """`standard` unset keeps what the project was exported with; None means core only."""
    entry = load_registry()["projects"].get(str(directory)) or {}
    keep = standard is ...
    resolved = load_standard(entry.get("standard") if keep else standard)
    name = profile or (entry.get("profile") if keep else None)
    if not name:
        resolved, name = detect_target(directory, resolved)
    if not resolved and not any(p.name == name for p in list_profiles(None)):
        for candidate in registered_standards():
            if any(p.name == name and p.origin == candidate.name for p in list_profiles(candidate)):
                resolved = candidate
                break
    return resolved, load_profile(name, resolved)


# ---------- per-project config ----------


def _is_plain(value: Any) -> bool:
    return isinstance(value, dict)


def _key_of(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("name", value.get("id"))
    return None


def deep_merge(base: Any, over: Any) -> Any:
    """Objects merge recursively; lists of named objects merge by name/id; other lists union."""
    if over is None:
        return base
    if isinstance(base, list) and isinstance(over, list):
        out = list(base)
        present = {json.dumps(item, sort_keys=True, default=str) for item in out}
        for item in over:
            key = _key_of(item)
            index = next((i for i, existing in enumerate(out) if key is not None and _key_of(existing) == key), -1)
            if index >= 0:
                out[index] = deep_merge(out[index], item)
                continue
            fingerprint = json.dumps(item, sort_keys=True, default=str)
            if fingerprint not in present:
                present.add(fingerprint)
                out.append(item)
        return out
    if _is_plain(base) and _is_plain(over):
        merged = dict(base)
        for key, value in over.items():
            merged[key] = deep_merge(base[key], value) if key in base else value
        return merged
    return over


def _tool_prefix(directory: Path) -> str:
    for lock, prefix in (
        ("uv.lock", "uv run "),
        ("poetry.lock", "poetry run "),
        ("pdm.lock", "pdm run "),
        ("Pipfile.lock", "pipenv run "),
    ):
        if (directory / lock).exists():
            return prefix
    return ""


def detect_config(directory: Path, profile: Profile, standard: Standard | None = None) -> dict:
    """The starting `standards.config.json` for a repo: its own tools, wired up."""
    deps = read_dependencies(directory)
    run = _tool_prefix(directory)
    has = lambda name: name in deps  # noqa: E731

    formatter = None
    if has("ruff"):
        formatter = {"command": f"{run}ruff format {{file}}", "globs": PY_GLOBS, "timeoutSec": 20}
    elif has("black"):
        formatter = {"command": f"{run}black --quiet {{file}}", "globs": PY_GLOBS, "timeoutSec": 30}

    linter = None
    if has("ruff"):
        linter = {"command": f"{run}ruff check --force-exclude {{file}}", "globs": PY_GLOBS, "timeoutSec": 30}
    elif has("flake8"):
        linter = {"command": f"{run}flake8 {{file}}", "globs": PY_GLOBS, "timeoutSec": 45}
    elif has("pylint"):
        linter = {"command": f"{run}pylint --score=n {{file}}", "globs": PY_GLOBS, "timeoutSec": 90}

    commands: dict[str, str] = {}
    if has("pytest") or is_dir(directory / "tests"):
        commands["test"] = f"{run}pytest"
    if linter:
        commands["lint"] = f"{run}ruff check ." if has("ruff") else linter["command"].replace(" {file}", " .")
    if has("mypy"):
        commands["typecheck"] = f"{run}mypy ."
    elif has("pyright"):
        commands["typecheck"] = f"{run}pyright"
    if formatter:
        commands["format"] = f"{run}ruff format ." if has("ruff") else f"{run}black ."
    commands["review"] = "mast check ."

    stop_checks: list[dict] = []
    if commands.get("typecheck"):
        stop_checks.append(
            {
                "name": "typecheck",
                "command": commands["typecheck"],
                "when": PY_GLOBS,
                "timeoutSec": 300,
                "enabled": False,
            }
        )

    related_tests = None
    if commands.get("test"):
        related_tests = {
            "command": f"{run}pytest -q {{changed}}",
            "globs": PY_GLOBS,
            "timeoutSec": 240,
            "mapToTests": True,
        }

    code_paths = [g for g in profile.code_paths if is_dir(directory / g.split("/")[0])]
    detected = {
        "enabled": True,
        "commands": commands,
        "format": formatter,
        "lint": linter,
        "analysis": dict(ANALYSIS_DEFAULTS),
        "changelog": {"file": "CHANGELOG.md", "requiredFor": code_paths} if (directory / "CHANGELOG.md").exists() else None,
        "stopChecks": stop_checks,
        "rules": [],
        "disabledRules": [],
        "severityOverrides": {},
        "protectedPaths": [],
        "bashGuards": [],
        "learning": {"captureCorrections": True, "windowDays": 30},
        "qa": {"enabled": True, "maxLoops": 3, "codeGlobs": PY_GLOBS, "relatedTests": related_tests},
        "review": {"enabled": True, "agent": "reviewer", "minChangedLines": 30, "blockOn": ["high", "medium"]},
        "commits": json.loads(json.dumps(COMMIT_DEFAULTS)),
    }

    config = deep_merge(deep_merge(detected, standard.config if standard else None), profile.config)
    fill = lambda cmd: cmd.replace("{run}", run) if isinstance(cmd, str) else cmd  # noqa: E731
    config["stopChecks"] = [
        {**{k: v for k, v in check.items() if k != "requiresDependency"}, "command": fill(check.get("command"))}
        for check in config.get("stopChecks") or []
        if not check.get("requiresDependency") or check["requiresDependency"] in deps
    ]
    for step in (config.get("format"), config.get("lint")):
        if step and step.get("command"):
            step["command"] = fill(step["command"])
    return config


# ---------- settings.json ----------

HOOK_FILES = {
    "SessionStart": ("session_start.py", 10, None),
    "UserPromptSubmit": ("prompt_submit.py", 5, None),
    "PreToolUse": ("pre_tool.py", 15, "Write|Edit|MultiEdit|NotebookEdit|Bash"),
    "PostToolUse": ("post_tool.py", 90, "Write|Edit|MultiEdit|NotebookEdit|Task|Agent"),
    "Stop": ("stop.py", 600, None),
}


def _our_hooks() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for event, (file, timeout, matcher) in HOOK_FILES.items():
        entry: dict[str, Any] = {
            "hooks": [
                {
                    "type": "command",
                    "command": f'python3 "$CLAUDE_PROJECT_DIR/{HOOK_MARK}{file}"',
                    "timeout": timeout,
                }
            ]
        }
        if matcher:
            entry = {"matcher": matcher, **entry}
        out[event] = [entry]
    return out


def strip_our_hooks(settings: dict) -> dict:
    for event, entries in list((settings.get("hooks") or {}).items()):
        kept = []
        for entry in entries:
            hooks = [h for h in entry.get("hooks", []) if HOOK_MARK not in str(h.get("command", ""))]
            if hooks:
                kept.append({**entry, "hooks": hooks})
        if kept:
            settings["hooks"][event] = kept
        else:
            settings["hooks"].pop(event, None)
    if "hooks" in settings and not settings["hooks"]:
        settings.pop("hooks")
    return settings


def merge_settings(file: Path) -> None:
    settings = strip_our_hooks(read_json_strict(file, {}) or {})
    settings.setdefault("hooks", {})
    for event, entries in _our_hooks().items():
        settings["hooks"][event] = [*settings["hooks"].get(event, []), *entries]
    write_json(file, settings)


# ---------- CLAUDE.md ----------


def claude_md_block(profile: Profile, config: dict, standard: Standard | None) -> str:
    imports = [
        f"@.claude/standards/packs/{pack}/{doc}"
        for pack in profile.packs
        for doc in pack_markdown(pack, standard)
    ]
    checks = " · ".join(f"{name}: `{command}`" for name, command in (config.get("commands") or {}).items())
    source = (
        f"Standard **{standard.name}**, profile **{profile.name}** (built on the mast core)."
        if standard
        else f"Profile **{profile.name}** (mast core)."
    )
    return "\n".join(
        [
            f"<!-- mast:start — {MANAGED_MARK} v{version()}. Edit the source repo and run `mast sync`; do not edit inside this block. -->",
            "## Python engineering standards",
            "",
            f"{source} Content in this file outside this block, and the project files "
            "`.claude/standards/local-wins.md` / `local-lessons.md`, is project-specific and **wins on conflict**.",
            "**Wins** sections list approaches the user loved — reproduce them. **Grievances** list mistakes the user corrected — never repeat them.",
            "",
            "`mast check <paths>` is the project's bug hunter and structure reviewer (AST-based, no network). "
            "Run it on what you changed before you say you are done; the hooks run it for you on every edit and at the end of each turn.",
            *( ["", f"Project checks — {checks}"] if checks else []),
            "",
            *imports,
            "@.claude/standards/local-wins.md",
            "@.claude/standards/local-lessons.md",
            "<!-- mast:end -->",
            "",
        ]
    )


def write_claude_md(directory: Path, profile: Profile, config: dict, standard: Standard | None) -> None:
    file = directory / "CLAUDE.md"
    if file.exists():
        text = file.read_text(encoding="utf-8")
    else:
        agents = next((f for f in ("AGENTS.md", ".cursor/AGENTS.md") if (directory / f).exists()), None)
        text = (
            f"# {directory.name}\n\n"
            "<!-- Project-specific context for Claude: architecture, commands, gotchas. -->\n"
            + (f"\n@{agents}\n" if agents else "")
        )
    block = claude_md_block(profile, config, standard)
    text = BLOCK_RE.sub(lambda _: block, text) if BLOCK_RE.search(text) else f"{text.rstrip()}\n\n{block}"
    file.write_text(text, encoding="utf-8")


# ---------- commands & agents ----------

ASSET_KINDS = ("commands", "agents")


def _is_managed(text: str) -> bool:
    return MANAGED_MARK in text


def managed_assets(directory: Path, kind: str) -> list[str]:
    asset_dir = directory / ".claude" / kind
    if not is_dir(asset_dir):
        return []
    return [
        f.name
        for f in sorted(asset_dir.glob("*.md"))
        if _is_managed(f.read_text(encoding="utf-8", errors="replace"))
    ]


def asset_sources(kind: str, standard: Standard | None) -> dict[str, tuple[Path, str]]:
    sources: dict[str, tuple[Path, str]] = {}
    for directory, label in [(TEMPLATES / kind, "mast")] + ([(standard.root / kind, f"standard {standard.name}")] if standard else []):
        if not is_dir(directory):
            continue
        for file in sorted(directory.glob("*.md")):
            sources[file.name] = (file, label)
    return sources


def with_managed_mark(content: str, label: str) -> str:
    if _is_managed(content):
        return content
    mark = f"<!-- {MANAGED_MARK} ({label}) — edit the source repo, then run `mast sync` -->\n"
    frontmatter = re.match(r"^---\n[\s\S]*?\n---\n", content)
    return frontmatter.group(0) + mark + content[frontmatter.end() :] if frontmatter else mark + content


# ---------- export / remove ----------


@dataclass
class ExportResult:
    dir: Path
    standard: str | None
    profile: str
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def export_project(
    target: str | Path,
    *,
    standard: str | Path | None | object = ...,
    profile: str | None = None,
    cursor: bool = False,
    dry_run: bool = False,
) -> ExportResult:
    require_data_root()
    directory = expand_home(target).resolve()
    if not is_dir(directory):
        raise MastError(f"Not a directory: {directory}")

    registry = load_registry()
    std, prof = resolve_target(directory, standard=standard, profile=profile)
    claude_dir = directory / ".claude"
    standards_dir = claude_dir / "standards"
    config_path = claude_dir / "standards.config.json"
    result = ExportResult(dir=directory, standard=std.name if std else None, profile=prof.name)

    def step(description: str, action) -> None:
        result.actions.append(description)
        if not dry_run:
            action()

    existing_config = read_json_strict(config_path, None)
    detected_config = detect_config(directory, prof, std)
    config = existing_config or detected_config

    def write_packs() -> None:
        shutil.rmtree(standards_dir / "packs", ignore_errors=True)
        for pack in prof.packs:
            source = pack_dir(pack, std)
            destination = standards_dir / "packs" / pack
            destination.mkdir(parents=True, exist_ok=True)
            for doc in pack_markdown(pack, std):
                shutil.copyfile(source / doc, destination / doc)
            if is_dir(source / "exemplars"):
                shutil.copytree(source / "exemplars", destination / "exemplars", dirs_exist_ok=True)

    step(f"standards packs → .claude/standards/packs ({', '.join(prof.packs)})", write_packs)

    rules = [rule for pack in prof.packs for rule in pack_rules(pack, std)]
    step(f"rules → .claude/standards/rules.json ({len(rules)} rules)", lambda: write_json(standards_dir / "rules.json", rules))

    def write_hooks() -> None:
        hooks_dir = standards_dir / "hooks"
        shutil.rmtree(hooks_dir, ignore_errors=True)
        hooks_dir.mkdir(parents=True, exist_ok=True)
        for file in sorted(HOOKS.glob("*.py")):
            shutil.copyfile(file, hooks_dir / file.name)
        # Vendor the analyzer so the hooks need nothing installed but Python.
        shutil.copytree(
            ANALYSIS_DIR,
            hooks_dir / "analysis",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    step("hooks + analyzer → .claude/standards/hooks", write_hooks)

    def create_once(rel: str, content: str, description: str) -> None:
        file = directory / rel
        if file.exists():
            return

        def write() -> None:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content, encoding="utf-8")

        step(description, write)

    create_once(".claude/standards/local-lessons.md", LOCAL_LESSONS, "create .claude/standards/local-lessons.md (project-owned)")
    create_once(".claude/standards/local-wins.md", LOCAL_WINS, "create .claude/standards/local-wins.md (project-owned)")
    create_once(".claude/standards/local-rules.json", "[]\n", "create .claude/standards/local-rules.json (project-owned)")
    create_once(
        ".claude/standards.config.json",
        json.dumps(config, indent=2) + "\n",
        "create .claude/standards.config.json (detected; project-owned)",
    )

    if existing_config:
        added = [key for key in detected_config if key not in existing_config]
        if added:
            step(
                f"config: add new defaults ({', '.join(added)})",
                lambda: write_json(config_path, {**existing_config, **{k: detected_config[k] for k in added}}),
            )

    for kind in ASSET_KINDS:
        noun = "command /" if kind == "commands" else "agent "
        sources = asset_sources(kind, std)
        for name in [n for n in managed_assets(directory, kind) if n not in sources]:
            step(f"remove retired {noun}{Path(name).stem}", lambda n=name: (claude_dir / kind / n).unlink())
        for name, (source_file, label) in sources.items():
            destination = claude_dir / kind / name
            if destination.exists() and not _is_managed(destination.read_text(encoding="utf-8", errors="replace")):
                result.warnings.append(f".claude/{kind}/{name} exists and is not managed — skipped {noun}{Path(name).stem}")
                continue

            def write(dest=destination, src=source_file, lbl=label) -> None:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(with_managed_mark(src.read_text(encoding="utf-8"), lbl), encoding="utf-8")

            step(f"{noun}{Path(name).stem} → .claude/{kind}/{name}", write)

    step("register hooks in .claude/settings.json", lambda: merge_settings(claude_dir / "settings.json"))
    step("CLAUDE.md managed block", lambda: write_claude_md(directory, prof, config, std))

    gitignore = directory / ".gitignore"
    if not (gitignore.exists() and ".claude/standards/learning/" in gitignore.read_text(encoding="utf-8", errors="replace")):

        def append_gitignore() -> None:
            previous = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
            separator = "" if not previous or previous.endswith("\n") else "\n"
            gitignore.write_text(f"{previous}{separator}\n{GITIGNORE_BLOCK}", encoding="utf-8")

        step(".gitignore local learning data", append_gitignore)

    if cursor:

        def write_cursor() -> None:
            rules_dir = directory / ".cursor" / "rules"
            rules_dir.mkdir(parents=True, exist_ok=True)
            for stale in rules_dir.glob("mast-*"):
                stale.unlink()
            for pack in prof.packs:
                for doc in pack_markdown(pack, std):
                    shutil.copyfile(pack_dir(pack, std) / doc, rules_dir / f"mast-{pack}-{Path(doc).stem}.mdc")

        step(".cursor/rules/mast-*.mdc (Cursor mirror)", write_cursor)

    manifest = {
        "version": version(),
        "standard": {"name": std.name, "root": str(std.root), "version": version(std.root)} if std else None,
        "profile": prof.name,
        "packs": prof.packs,
        "cursor": cursor,
        "hash": source_hash(prof, std),
        "exportedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    def write_manifest() -> None:
        write_json(standards_dir / "manifest.json", manifest)
        registry["projects"][str(directory)] = {
            "standard": str(std.root) if std else None,
            "profile": prof.name,
            "cursor": cursor,
            "exportedAt": manifest["exportedAt"],
            "version": manifest["version"],
        }
        save_registry(registry)

    step("manifest + registry", write_manifest)
    return result


def remove_project(target: str | Path, *, purge: bool = False) -> tuple[Path, list[str]]:
    directory = expand_home(target).resolve()
    standards_dir = directory / ".claude" / "standards"
    removed: list[str] = []

    claude_md = directory / "CLAUDE.md"
    if claude_md.exists():
        text = claude_md.read_text(encoding="utf-8")
        if BLOCK_RE.search(text):
            claude_md.write_text(BLOCK_RE.sub("", text).rstrip() + "\n", encoding="utf-8")
            removed.append("CLAUDE.md block")

    settings_file = directory / ".claude" / "settings.json"
    if settings_file.exists():
        write_json(settings_file, strip_our_hooks(read_json_strict(settings_file, {}) or {}))
        removed.append("settings.json hooks")

    for kind in ASSET_KINDS:
        for name in managed_assets(directory, kind):
            (directory / ".claude" / kind / name).unlink()
            removed.append(f".claude/{kind}/{name}")

    managed: Iterable[Path] = (
        [standards_dir, directory / ".claude" / "standards.config.json"]
        if purge
        else [standards_dir / p for p in ("packs", "hooks", "rules.json", "manifest.json", ".state")]
    )
    for path in managed:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink()
            removed.append(str(path.relative_to(directory)))

    cursor_rules = directory / ".cursor" / "rules"
    if is_dir(cursor_rules):
        for stale in cursor_rules.glob("mast-*"):
            stale.unlink()
            removed.append(f".cursor/rules/{stale.name}")

    registry = load_registry()
    registry["projects"].pop(str(directory), None)
    save_registry(registry)
    return directory, removed
