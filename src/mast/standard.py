"""Standards are separate repos layered on this core: packs, profiles, detection, config."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .analysis.rules import compile_rule
from .common import (
    STANDARD_FILE,
    MastError,
    Standard,
    expand_home,
    is_dir,
    list_packs,
    list_profiles,
    load_registry,
    load_standard,
    pack_dir,
    read_json,
    save_registry,
)


def wins_header(pack: str) -> str:
    return (
        f"---\ndescription: Wins promoted into the {pack} pack — approaches the user loved. Reproduce them.\nalwaysApply: true\n---\n\n"
        f"# Wins — reproduce these ({pack})\n\nWhen a task resembles one of these, follow the same approach. "
        "When an exemplar is listed, read it before starting and match its quality bar.\n\n"
        "<!-- mast:wins — appended by `mast promote` -->\n"
    )


def grievances_header(pack: str) -> str:
    return (
        f"---\ndescription: Grievances promoted into the {pack} pack — mistakes the user corrected. Never repeat them.\nalwaysApply: true\n---\n\n"
        f"# Grievances — never repeat ({pack})\n\nEach entry cost the user time in a real session. Treat them as hard rules.\n\n"
        "<!-- mast:grievances — appended by `mast promote` -->\n"
    )


def register_standard(ref: str | Path) -> Standard:
    standard = load_standard(expand_home(ref).resolve())
    if standard is None:
        raise MastError(f"{ref} is not a standard.")
    registry = load_registry()
    registry["standards"][standard.name] = str(standard.root)
    save_registry(registry)
    return standard


def unregister_standard(name: str) -> None:
    registry = load_registry()
    root = registry["standards"].get(name)
    if not root:
        raise MastError(f'No registered standard "{name}".')
    in_use = [d for d, entry in registry["projects"].items() if entry.get("standard") == root]
    if in_use:
        raise MastError(f'{len(in_use)} project(s) still use "{name}" — re-export them first.')
    registry["standards"].pop(name)
    save_registry(registry)


def init_standard(target: str | Path, *, name: str | None = None, description: str | None = None) -> dict:
    root = expand_home(target).resolve()
    if (root / STANDARD_FILE).exists():
        raise MastError(f"{root} is already a standard.")
    raw = (name or root.name).lower()
    raw = re.sub(r"[-_.]?(python-)?standards?$", "", raw)
    slug = re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", raw)) or "my"
    title = slug[:1].upper() + slug[1:]
    shared = f"{slug}-shared"
    dumps = lambda value: json.dumps(value, indent=2) + "\n"  # noqa: E731

    files = {
        STANDARD_FILE: dumps(
            {
                "name": slug,
                "description": description or f"{title} Python engineering standards for Claude Code, layered on the mast core.",
                "core": f">={__version__}",
                "defaultPack": shared,
                "defaultProfile": slug,
                "detect": [],
                "config": {},
                "privateTerms": [],
            }
        ),
        f"profiles/{slug}.json": dumps(
            {
                "description": f"Core + {title} shared conventions",
                "packs": ["core", shared],
                "codePaths": ["src/**", "app/**", "tests/**"],
            }
        ),
        f"packs/{shared}/{slug}.md": (
            f"---\ndescription: {title} — context, conventions and hard stops shared by every {title} Python repo\nalwaysApply: true\n---\n\n"
            f"# {title} Python standards\n\n<!-- What every {title} repo shares: services, domain vocabulary, "
            "layering rules, approved libraries, hard stops. -->\n"
        ),
        f"packs/{shared}/wins.md": wins_header(shared),
        f"packs/{shared}/grievances.md": grievances_header(shared),
        f"packs/{shared}/rules.json": "[]\n",
        "commands/.gitkeep": "",
        ".gitignore": "__pycache__/\n.venv/\n.DS_Store\n",
        "README.md": (
            f"# {slug}-standards\n\n{title} Python engineering standards for Claude Code, layered on the "
            "[mast](https://github.com/zugobite/mast) core.\n\n"
            f"- `packs/` — {title}-specific packs (core packs such as `core`, `web`, `data` are reused from the core)\n"
            "- `profiles/` — which packs a kind of repo gets\n"
            f"- `{STANDARD_FILE}` — detection rules, default pack for promoted lessons, config defaults\n"
            "- `commands/`, `agents/` — extra Claude slash commands and agents exported to every repo\n\n"
            "```bash\nmast standard check .      # validate\nmast export <repo>         # auto-detects your repos\n"
            "mast sync                  # push changes to every exported repo\n```\n"
        ),
    }

    for rel, content in files.items():
        file = root / rel
        if file.exists():
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    if not is_dir(root / ".git"):
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=False)
    register_standard(root)
    return {"root": root, "name": slug, "files": list(files)}


def _satisfies(have: str, wanted: str) -> bool:
    match = re.match(r"^>=\s*(\d+)\.(\d+)\.(\d+)", str(wanted))
    if not match:
        return True
    current = [int(p) for p in re.findall(r"\d+", have)[:3]] or [0, 0, 0]
    current += [0] * (3 - len(current))
    for index in range(3):
        want = int(match.group(index + 1))
        if current[index] != want:
            return current[index] > want
    return True


@dataclass
class CheckResult:
    ok: bool
    label: str
    hint: str = ""


def check_standard(target: str | Path) -> tuple[Standard | None, list[CheckResult]]:
    results: list[CheckResult] = []
    record = lambda ok, label, hint="": results.append(CheckResult(bool(ok), label, hint))  # noqa: E731

    try:
        standard = load_standard(expand_home(target).resolve())
    except MastError as exc:
        record(False, STANDARD_FILE, str(exc))
        return None, results
    if standard is None:
        record(False, STANDARD_FILE, "not a standard")
        return None, results

    record(bool(re.fullmatch(r"[a-z0-9-]+", standard.name)), f'name "{standard.name}"', "use lowercase letters, digits and dashes")
    record(
        not standard.core or _satisfies(__version__, standard.core),
        f'core {__version__} satisfies "{standard.core or "any"}"',
        "upgrade mast",
    )

    profiles = list_profiles(standard)
    own = [p for p in profiles if p.origin == standard.name]
    record(bool(own), f"{len(own)} profile(s) defined", "add profiles/<name>.json")
    for profile in own:
        missing = []
        for pack in profile.packs:
            try:
                pack_dir(pack, standard)
            except MastError:
                missing.append(pack)
        record(
            bool(profile.packs) and not missing,
            f"profile {profile.name}: {' + '.join(profile.packs)}",
            f"unknown pack(s): {', '.join(missing)}" if missing else "no packs",
        )
    record(
        not standard.default_profile or any(p.name == standard.default_profile for p in profiles),
        f"defaultProfile {standard.default_profile or '—'}",
        "not a known profile",
    )
    default_pack_ok = True
    if standard.default_pack:
        try:
            pack_dir(standard.default_pack, standard)
        except MastError:
            default_pack_ok = False
    record(default_pack_ok, f"defaultPack {standard.default_pack or '— (promotions go to core)'}", "not a known pack")

    for index, rule in enumerate(standard.detect):
        ok = any(p.name == rule.get("profile") for p in profiles)
        hint = f"unknown profile {rule.get('profile')}"
        if ok and rule.get("name"):
            try:
                re.compile(rule["name"])
            except re.error:
                ok, hint = False, "invalid name regex"
        record(ok, f"detect[{index}] {'/' + rule['name'] + '/ ' if rule.get('name') else ''}→ {rule.get('profile')}", hint)

    seen: dict[str, int] = {}
    for pack in list_packs(standard):
        rules = read_json(pack.dir / "rules.json", []) or []
        for rule in rules:
            if isinstance(rule, dict) and rule.get("id"):
                seen[rule["id"]] = seen.get(rule["id"], 0) + 1
        if pack.origin != standard.name:
            continue
        docs = sorted(pack.dir.glob("*.md"))
        without = [d.name for d in docs if not re.match(r"^---\n[\s\S]*?description:", d.read_text(encoding="utf-8", errors="replace"))]
        record(not without, f"pack {pack.name}: {len(docs)} doc(s) with frontmatter", f"missing description frontmatter: {', '.join(without)}")
        bad = [
            rule.get("id") or "?"
            for rule in rules
            if not isinstance(rule, dict)
            or not rule.get("id")
            or not rule.get("message")
            or rule.get("severity") not in (None, "warn", "block")
            or compile_rule(rule) is None
        ]
        record(not bad, f"pack {pack.name}: {len(rules)} rule(s) valid", f"invalid: {', '.join(bad)}")

    duplicates = [rule_id for rule_id, count in seen.items() if count > 1]
    record(not duplicates, "rule ids unique across core + standard", f"duplicates: {', '.join(duplicates)}")
    return standard, results
