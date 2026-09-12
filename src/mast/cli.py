"""The `mast` command line: check Python code, and export standards + hooks to projects."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from . import __version__
from .analysis import REGISTRY, Settings, all_checks, analyze as analyze_code, resolve_selection
from .analysis.render import paint, render_json, render_sarif, render_text
from .analysis.rules import active_rules, invalid_rules
from .common import (
    PROFILES,
    ROOT,
    STANDARDS,
    MastError,
    c,
    expand_home,
    is_dir,
    list_packs,
    list_profiles,
    load_profile,
    load_registry,
    load_standard,
    pack_rules,
    pad,
    read_json,
    read_jsonl,
    registered_standards,
    source_hash,
    tildify,
    version,
    which,
)
from .config import find_project_root, load_settings
from .export import export_project, remove_project, resolve_target
from .leakcheck import leakcheck
from .learn import REPORT, analyze, collect_candidates, distill, promote, reject, render_report
from .standard import check_standard, init_standard, register_standard, unregister_standard

EPILOG = """examples:
  mast check src tests            hunt for bugs, inefficiency and structure problems
  mast check --diff main          only what this branch changed
  mast explain bug/mutable-default        why it matters and how to fix it
  mast export ~/code/my-api       standards + hooks into a repo (auto-detects the stack)
  mast doctor ~/code/my-api       verify the setup and self-test the hooks

environment:
  MAST_ROOTS        directories the interactive picker scans (default ~/Developer)
  MAST_STATE_DIR    per-user state (default ~/.mast)
  MAST_HOME         where mast's own standards/, hooks/ and templates/ live
"""


# ---------- check ----------


def _changed_lines(ref: str, paths: list[str], root: Path) -> dict[str, set[int]]:
    """Lines added or changed since `ref`, per file — for reviewing only the current diff."""
    args = ["git", "diff", "--unified=0", "--no-color", ref, "--", *(paths or ["."])]
    try:
        result = subprocess.run(args, cwd=str(root), capture_output=True, text=True, check=False)
    except OSError as exc:
        raise MastError(f"git diff failed: {exc}") from exc
    if result.returncode != 0:
        raise MastError(f"git diff against {ref!r} failed: {result.stderr.strip()}")

    changed: dict[str, set[int]] = {}
    current: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            changed.setdefault(current, set())
        elif line.startswith("@@") and current:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2) or 1)
                changed[current].update(range(start, start + count))
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *(paths or ["."])],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    for rel in untracked.stdout.splitlines():
        if rel.endswith(".py"):
            try:
                total = len((root / rel).read_text(encoding="utf-8").splitlines())
            except OSError:
                continue  # a deleted or unreadable untracked file has no lines to review
            changed[rel] = set(range(1, total + 2))
    return changed


def cmd_check(args: argparse.Namespace) -> int:
    root = find_project_root(args.paths[0] if args.paths else ".")
    settings, source = load_settings(root, config_file=args.config, select=args.select, ignore=args.ignore)
    changed = _changed_lines(args.diff, args.paths, root) if args.diff else None
    report = analyze_code(args.paths or ["."], settings, root=root, changed_lines=changed)

    if args.format == "json":
        sys.stdout.write(render_json(report))
    elif args.format == "sarif":
        sys.stdout.write(render_sarif(report, version=__version__))
    else:
        if args.stats:
            where = f" · settings from {source}" if source else " · default settings"
            print(paint(f"mast {__version__} · {report.checks} checks{where}", "dim"))
        print(render_text(report, show_source=not args.no_source, explain=args.explain))
    return 1 if report.exceeds(args.fail_on) else 0


def cmd_explain(args: argparse.Namespace) -> int:
    ids = sorted(resolve_selection(args.checks))
    if not ids:
        raise MastError("Nothing matched. `mast checks` lists every check.")
    for index, check_id in enumerate(ids):
        meta = REGISTRY[check_id]
        if index:
            print()
        print(f"{c.bold(meta.id)}  {c.dim(f'{meta.category} · {meta.severity}')}")
        print(f"  {meta.title}")
        print()
        print(f"  {c.bold('Why it matters')}")
        for line in _wrap(meta.why):
            print(f"  {line}")
        print()
        print(f"  {c.bold('How to fix it')}")
        for line in _wrap(meta.fix):
            print(f"  {line}")
    return 0


def _wrap(text: str, width: int = 92) -> list[str]:
    import textwrap

    return textwrap.wrap(" ".join(text.split()), width=width) or [""]


def cmd_checks(args: argparse.Namespace) -> int:
    checks = [
        check
        for check in all_checks()
        if (not args.category or check.category == args.category) and (not args.severity or check.severity == args.severity)
    ]
    if args.format == "json":
        sys.stdout.write(json.dumps([check.as_dict() for check in checks], indent=2) + "\n")
        return 0
    category = None
    for check in checks:
        if check.category != category:
            category = check.category
            print(f"\n{c.bold(category)}")
        print(f"  {pad(c.cyan(check.id), 34)}{pad(check.severity, 8)}{check.title}")
    print(f"\n{len(checks)} check(s). `mast explain <id>` for the reasoning and the fix.")
    return 0


# ---------- export ----------


def _safe_standard(ref):
    try:
        return load_standard(ref)
    except MastError:
        return False  # the standard repo is missing on disk


def _standard_name(root):
    if not root:
        return None
    standard = _safe_standard(root)
    return standard.name if standard else f"missing:{tildify(root)}"


def _label(standard_name, profile_name) -> str:
    return f"{standard_name + '/' if standard_name else ''}{profile_name}"


def _print_catalogue(standard) -> None:
    for profile in list_profiles(standard):
        if standard and profile.origin != standard.name:
            continue
        print(f"  {pad(c.cyan(profile.name), 28)}{pad(' + '.join(profile.packs), 40)}{c.dim(profile.description)}")
    packs = [p for p in list_packs(standard) if not standard or p.origin == standard.name]
    described = [f"{p.name} ({len(list(p.dir.glob('*.md')))} docs, {len(pack_rules(p.name, standard))} rules)" for p in packs]
    if described:
        print(c.dim(f"  packs: {' · '.join(described)}"))


def cmd_list(args: argparse.Namespace) -> int:
    print(c.bold(f"Core  {c.dim(f'{tildify(ROOT)} v{version()}')}"))
    print(c.dim(f"  {len(REGISTRY)} analyzer checks · `mast checks` to list them"))
    _print_catalogue(None)
    for standard in registered_standards():
        print(c.bold(f"\nStandard {standard.name}  {c.dim(f'{tildify(standard.root)} v{version(standard.root)}')}"))
        if standard.description:
            print(c.dim(f"  {standard.description}"))
        _print_catalogue(standard)
    print(c.bold("\nExported projects"))
    projects = load_registry()["projects"]
    if not projects:
        print(c.dim("  none yet — run `mast export`"))
    for directory, entry in projects.items():
        suffix = " +cursor" if entry.get("cursor") else ""
        print(f"  {pad(tildify(directory), 50)}{_label(_standard_name(entry.get('standard')), entry.get('profile'))}{suffix}")
    return 0


def _ask(question: str) -> str:
    try:
        return input(question).strip()
    except EOFError:
        return ""


def _parse_selection(text: str, count: int) -> list[int]:
    if re.fullmatch(r"all", text.strip(), re.IGNORECASE):
        return list(range(count))
    picked: set[int] = set()
    for token in re.split(r"[\s,]+", text.strip()):
        if not token:
            continue
        bounds = token.split("-")
        try:
            low = int(bounds[0])
            high = int(bounds[1]) if len(bounds) > 1 and bounds[1] else low
        except ValueError:
            continue  # ignore tokens that are not numbers or ranges
        picked.update(i - 1 for i in range(low, high + 1) if 1 <= i <= count)
    return sorted(picked)


def _picker_dirs() -> list[Path]:
    import os

    roots = (os.environ.get("MAST_ROOTS") or "~/Developer").split(":")
    out: list[Path] = []
    for raw in roots:
        root = expand_home(raw)
        if not is_dir(root):
            continue
        for entry in sorted(root.iterdir()):
            if is_dir(entry / ".git") and not (entry / "standard.json").exists() and entry != ROOT:
                out.append(entry)
    return out


def _print_export(result, dry_run: bool) -> None:
    suffix = c.yellow(" [dry run]") if dry_run else ""
    print(f"\n{c.bold(tildify(result.dir))} {c.dim(f'({_label(result.standard, result.profile)})')}{suffix}")
    for action in result.actions:
        print(f"  {c.green('✓')} {action}")
    for warning in result.warnings:
        print(f"  {c.yellow('!')} {warning}")


def cmd_export(args: argparse.Namespace) -> int:
    targets: list[str] = list(args.dirs)
    registry = load_registry()
    standard_ref = args.standard if args.standard is not None else ...

    if not targets:
        if not sys.stdin.isatty():
            raise MastError("Pass project paths, or run in a terminal for the interactive picker.")
        dirs = _picker_dirs()
        if not dirs:
            raise MastError("No git repos found under MAST_ROOTS.")
        for index, directory in enumerate(dirs, start=1):
            entry = registry["projects"].get(str(directory))
            if entry:
                tag = c.green(f"exported · {_label(_standard_name(entry.get('standard')), entry.get('profile'))}")
            else:
                try:
                    std, profile = resolve_target(directory, standard=standard_ref)
                    tag = c.dim(f"detected: {_label(std.name if std else None, profile.name)}")
                except MastError as exc:
                    tag = c.red(str(exc))
            print(f"{str(index).rjust(3)}. {pad(directory.name, 42)}{tag}")
        targets = [str(dirs[i]) for i in _parse_selection(_ask("\nSelect projects (e.g. 1 3 5, 2-4, all): "), len(dirs))]

    if not targets:
        print("Nothing selected.")
        return 0

    plan = []
    for target in targets:
        directory = expand_home(target).resolve()
        std, profile = resolve_target(directory, standard=standard_ref, profile=args.profile)
        plan.append((directory, std, profile))

    print(c.bold("\nPlan"))
    for directory, std, profile in plan:
        print(f"  {pad(tildify(directory), 50)}→ {c.cyan(_label(std.name if std else None, profile.name))}")
    if not args.yes and not args.dry_run and sys.stdin.isatty():
        if re.match(r"^n", _ask("Proceed? [Y/n] "), re.IGNORECASE):
            return 0

    for directory, std, profile in plan:
        result = export_project(
            directory,
            standard=str(std.root) if std else None,
            profile=profile.name,
            cursor=bool(args.cursor or (registry["projects"].get(str(directory)) or {}).get("cursor")),
            dry_run=args.dry_run,
        )
        _print_export(result, args.dry_run)
    if not args.dry_run:
        print(f"\n{c.dim('Review with `git diff`, commit .claude/ + CLAUDE.md, and check a project with `mast doctor <dir>`.')}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    projects = load_registry()["projects"]
    if not projects:
        print("No exported projects. Run `mast export` first.")
        return 0
    for directory, entry in projects.items():
        if not is_dir(directory):
            print(f"{c.yellow('!')} {tildify(directory)} no longer exists — `mast remove {tildify(directory)}` to unregister")
            continue
        try:
            result = export_project(
                directory,
                standard=entry.get("standard"),
                profile=entry.get("profile"),
                cursor=bool(entry.get("cursor")),
                dry_run=args.dry_run,
            )
            detail = f"({_label(result.standard, result.profile)}, {len(result.actions)} steps{', dry run' if args.dry_run else ''})"
            print(f"{c.green('✓')} {tildify(directory)} {c.dim(detail)}")
            for warning in result.warnings:
                print(f"  {c.yellow('!')} {warning}")
        except MastError as exc:
            print(f"{c.red('✗')} {tildify(directory)} {exc}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    projects = load_registry()["projects"]
    if not projects:
        print("No exported projects. Run `mast export` first.")
        return 0
    pending: dict[str, int] = {}
    for candidate in collect_candidates():
        for project in candidate["projects"]:
            pending[project] = pending.get(project, 0) + 1

    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    header = f"{pad('project', 44)}{pad('standard/profile', 28)}{pad('state', 13)}viol  👍    👎    gates  find  pending"
    print(c.bold(header))
    for directory, entry in projects.items():
        standard = _safe_standard(entry["standard"]) if entry.get("standard") else None
        manifest = read_json(Path(directory) / ".claude" / "standards" / "manifest.json", None)
        state = c.red("missing")
        if standard is False:
            state = c.red("std missing")
        elif manifest:
            try:
                current = source_hash(load_profile(entry["profile"], standard or None), standard or None)
                state = c.green("up to date") if manifest.get("hash") == current else c.yellow("needs sync")
            except MastError:
                state = c.red("bad profile")
        events = []
        for event in read_jsonl(Path(directory) / ".claude" / "standards" / "learning" / "events.jsonl"):
            try:
                when = datetime.fromisoformat(str(event.get("ts")).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue  # an event without a usable timestamp is ignored
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when >= cutoff:
                events.append(event)
        count = lambda kind: str(sum(1 for e in events if e["type"] == kind)).ljust(5)  # noqa: E731
        print(
            f"{pad(tildify(directory), 44)}{pad(_label(_standard_name(entry.get('standard')), entry.get('profile')), 28)}"
            f"{pad(state, 13)}{count('violation')} {count('praise')} {count('correction')} {count('gate_block')} "
            f"{count('analysis_finding')} {pending.get(directory, 0)}"
        )
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    if not args.yes and sys.stdin.isatty():
        what = (
            "managed files, hooks, config AND learning data"
            if args.purge
            else "managed files and hooks (keeps learning data + config)"
        )
        if not re.match(r"^y", _ask(f"Remove {what} from {args.dir}? [y/N] "), re.IGNORECASE):
            return 0
    directory, removed = remove_project(args.dir, purge=args.purge)
    print(c.bold(tildify(directory)))
    for item in removed:
        print(f"  {c.red('−')} {item}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    directory = expand_home(args.dir or ".").resolve()
    standards_dir = directory / ".claude" / "standards"
    failures = 0

    def check(ok: bool, text: str, hint: str = "") -> None:
        nonlocal failures
        if not ok:
            failures += 1
        mark = c.green("✓") if ok else c.red("✗")
        print(f"  {mark} {text}{c.dim(f' — {hint}') if not ok and hint else ''}")

    def run_hook(name: str, payload: dict):
        import os

        return subprocess.run(
            [sys.executable, str(standards_dir / "hooks" / name)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(directory), "MAST_NO_LOG": "1"},
            check=False,
        )

    print(c.bold(tildify(directory)))
    check(sys.version_info >= (3, 10), f"python {sys.version.split()[0]}", "the hooks need Python ≥ 3.10")
    manifest = read_json(standards_dir / "manifest.json", None)
    label = _label((manifest.get("standard") or {}).get("name") if manifest else None, manifest.get("profile") if manifest else "—")
    check(bool(manifest), f"exported ({label} · core v{manifest.get('version') if manifest else '—'})", "run `mast export`")
    if not manifest:
        return 1

    standard = _safe_standard((manifest.get("standard") or {}).get("root")) if manifest.get("standard") else None
    if manifest.get("standard"):
        check(
            standard is not False,
            f"standard {manifest['standard']['name']} at {tildify(manifest['standard']['root'])}",
            "standard repo missing — clone it and `mast standard add <dir>`",
        )
    in_sync = False
    try:
        in_sync = manifest.get("hash") == source_hash(load_profile(manifest["profile"], standard or None), standard or None)
    except MastError:
        pass  # a missing standard or profile is already reported above
    check(in_sync, "in sync with source", "run `mast sync`")

    settings = read_json(directory / ".claude" / "settings.json", {}) or {}
    events = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]
    wired = [e for e in events if ".claude/standards/hooks/" in json.dumps((settings.get("hooks") or {}).get(e, []))]
    check(len(wired) == len(events), f"hooks registered ({len(wired)}/{len(events)})", "re-run `mast export`")

    config = read_json(directory / ".claude" / "standards.config.json", None)
    check(config is not None, "standards.config.json parses", "fix JSON syntax")
    rules = active_rules(
        [
            read_json(standards_dir / "rules.json", []) or [],
            read_json(standards_dir / "local-rules.json", []) or [],
            (config or {}).get("rules") or [],
        ],
        config or {},
    )
    broken = invalid_rules(rules)
    check(not broken, f"{len(rules)} text rules compile", f"invalid: {', '.join(r.get('id', '?') for r in broken)}")

    vendored = standards_dir / "hooks" / "analysis"
    check(vendored.is_dir(), "analyzer vendored into .claude/standards/hooks/analysis", "re-run `mast export`")
    probe = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import analysis; print(len(analysis.REGISTRY))", str(standards_dir / "hooks")],
        capture_output=True,
        text=True,
        check=False,
    )
    check(probe.returncode == 0, f"vendored analyzer loads ({probe.stdout.strip() or '0'} checks)", probe.stderr.strip()[:120])

    for name, step in (("format", (config or {}).get("format")), ("lint", (config or {}).get("lint"))):
        if not step or not step.get("command"):
            continue
        binary = step["command"].split()[0]
        check(bool(which(binary)), f"{name}: {step['command']}", f"{binary} not on PATH")

    denied = lambda result: '"permissionDecision": "deny"' in result.stdout or '"permissionDecision":"deny"' in result.stdout  # noqa: E731
    bad_write = {"tool_name": "Write", "tool_input": {"file_path": "mast_doctor_probe.py", "content": "import os\nos.system(f'rm {x}')\n"}}
    check(denied(run_hook("pre_tool.py", bad_write)), "pre-tool blocks a write with a high-severity finding")
    good_write = {"tool_name": "Write", "tool_input": {"file_path": "mast_doctor_probe.py", "content": "VALUE: int = 1\n"}}
    check(not run_hook("pre_tool.py", good_write).stdout.strip(), "pre-tool allows clean writes")
    force_push = {"tool_name": "Bash", "tool_input": {"command": "git push --force origin feature"}}
    check(denied(run_hook("pre_tool.py", force_push)), "pre-tool blocks force-push")
    session = run_hook("session_start.py", {"hook_event_name": "SessionStart"})
    check(session.returncode == 0 and "additionalContext" in session.stdout, "session-start emits context")

    claude_md = directory / "CLAUDE.md"
    text = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    check("<!-- mast:start" in text, "CLAUDE.md imports standards", "re-run `mast export`")

    print(c.red(f"\n{failures} problem(s)") if failures else c.green("\nAll good."))
    return 1 if failures else 0


# ---------- standards ----------


def cmd_standard(args: argparse.Namespace) -> int:
    sub = args.standard_command
    if sub == "init":
        result = init_standard(args.dir, name=args.name, description=args.description)
        print(f"{c.green('✓')} standard {c.cyan(result['name'])} created and registered at {tildify(result['root'])}")
        for file in result["files"]:
            print(f"  {c.dim(file)}")
        print(c.dim("\nNext: write packs/, add profiles + detect rules to standard.json, then `mast standard check` and `mast export`."))
        return 0
    if sub == "add":
        standard = register_standard(args.dir)
        print(f"{c.green('✓')} registered {c.cyan(standard.name)} → {tildify(standard.root)}")
        return 0
    if sub == "remove":
        unregister_standard(args.name)
        print(f"{c.dim('−')} unregistered {args.name}")
        return 0
    if sub == "check":
        standard, results = check_standard(args.dir or ".")
        print(c.bold(f"{standard.name}  {c.dim(tildify(standard.root))}" if standard else str(args.dir or ".")))
        for result in results:
            mark = c.green("✓") if result.ok else c.red("✗")
            print(f"  {mark} {result.label}{c.dim(f' — {result.hint}') if not result.ok and result.hint else ''}")
        failed = sum(1 for r in results if not r.ok)
        print(c.red(f"\n{failed} problem(s)") if failed else c.green("\nStandard is valid."))
        return 1 if failed else 0

    registry = load_registry()
    if not registry["standards"]:
        print(c.dim("No standards registered — `mast standard init <dir>` or `mast standard add <dir>`."))
        return 0
    for name, root in registry["standards"].items():
        users = sum(1 for entry in registry["projects"].values() if entry.get("standard") == root)
        missing = "" if _safe_standard(root) else c.red(" missing on disk")
        print(f"  {pad(c.cyan(name), 20)}{pad(tildify(root), 44)}{users} project(s){missing}")
    return 0


def cmd_leakcheck(args: argparse.Namespace) -> int:
    report = leakcheck(args.dir or ROOT, terms=[args.term] if args.term else [])
    print(c.bold(f"{tildify(report.root)} — {report.files} publishable file(s), {len(report.terms)} private term(s) checked"))
    if not report.hits:
        print(c.green("✓ No leaks found."))
        return 0
    for hit in report.hits:
        shown = f"{hit['match'][:4]}…" if hit["check"] in ("secret", "credential") else hit["match"]
        kind = c.yellow(f"[{hit['check']}]")
        print(f"  {c.red('✗')} {hit['file']}:{hit['line']} {kind} {shown}")
    print(c.red(f"\n{len(report.hits)} potential leak(s) — fix before publishing."))
    return 1


# ---------- learn ----------


def cmd_learn(args: argparse.Namespace) -> int:
    if args.distill:
        print(c.dim("Distilling praise and corrections with `claude -p`…"))
        result = distill(days=args.days)
        print(result.get("note") or f"{c.green('✓')} {len(result['added'])} new candidate(s)")
    analysis = analyze(days=args.days)
    report = render_report(analysis)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report, encoding="utf-8")

    print(c.bold(f"mast learning report  {c.dim(f'last {analysis.days} days · {len(analysis.projects)} projects · {analysis.events} events')}"))
    if analysis.recommendations:
        print(c.bold("\nRecommendations"))
        for item in analysis.recommendations:
            print(f"  • {re.sub(r'[*_]{1,2}', '', item)}")
    wins = [x for x in analysis.candidates if x["kind"] == "win"]
    grievances = [x for x in analysis.candidates if x["kind"] == "grievance"]
    for title, rows in (("Pending wins", wins), ("Pending grievances", grievances)):
        if not rows:
            continue
        print(c.bold(f"\n{title}"))
        for row in rows:
            print(f"  {c.cyan(row['id'])} {pad(row.get('scope', 'global'), 9)}{row['lesson']}")
    print(c.dim(f"\nFull report: {tildify(REPORT)}"))
    if analysis.candidates:
        print(c.dim("Promote with `mast promote <id>`, dismiss with `mast reject <id>`, then `mast sync`."))
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    result = promote(args.id, scope=args.scope, pack=args.pack)
    candidate = result["candidate"]
    where = f"{result['target']['name']} pack {result['pack']}" if result["scope"] == "global" else "this project"
    print(f"{c.green('✓')} promoted {c.cyan(candidate['id'])} ({candidate['kind']}) into {where}")
    for file in result["written"]:
        print(f"  {c.dim(file)}")
    if args.sync:
        return cmd_sync(argparse.Namespace(dry_run=False))
    print(c.dim("Run `mast sync` to push it into every exported project."))
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    candidate = reject(args.id)
    print(f"{c.dim('−')} dismissed {candidate['id']}: {candidate['lesson']}")
    return 0


# ---------- parser ----------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mast",
        description="Python code reviewer, bug hunter and enterprise standards for Claude Code.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"mast {version()}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    check = subparsers.add_parser("check", help="analyze Python files for bugs, inefficiency and structure problems")
    check.add_argument("paths", nargs="*", help="files or directories (default: the current directory)")
    check.add_argument("--format", choices=("text", "json", "sarif"), default="text")
    check.add_argument("--fail-on", choices=("high", "medium", "low", "none"), default="high", help="exit 1 when a finding reaches this severity (default: high)")
    check.add_argument("--select", action="append", metavar="ID", help="only these checks, categories or prefixes (repeatable)")
    check.add_argument("--ignore", action="append", metavar="ID", help="skip these checks, categories or prefixes (repeatable)")
    check.add_argument("--diff", metavar="REF", help="only report findings on lines changed since REF (e.g. main)")
    check.add_argument("--explain", action="store_true", help="include the why and the fix with each finding")
    check.add_argument("--no-source", action="store_true", help="do not print the offending source line")
    check.add_argument("--stats", action="store_true", help="print the settings and check count first")
    check.add_argument("--config", metavar="FILE", help="read analyzer settings from this JSON file")
    check.set_defaults(func=cmd_check)

    explain = subparsers.add_parser("explain", help="why a check exists and how to fix what it finds")
    explain.add_argument("checks", nargs="+", metavar="CHECK", help="check id, category or prefix")
    explain.set_defaults(func=cmd_explain)

    checks = subparsers.add_parser("checks", help="list every analyzer check")
    checks.add_argument("--category", choices=("bugs", "efficiency", "structure", "standards", "security"))
    checks.add_argument("--severity", choices=("high", "medium", "low"))
    checks.add_argument("--format", choices=("text", "json"), default="text")
    checks.set_defaults(func=cmd_checks)

    listing = subparsers.add_parser("list", help="core + registered standards: profiles, packs, exported projects")
    listing.set_defaults(func=cmd_list)

    export = subparsers.add_parser("export", help="export standards + hooks into repos")
    export.add_argument("dirs", nargs="*")
    export.add_argument("--standard", metavar="NAME|PATH", help="layer a standard on the core")
    export.add_argument("--profile", metavar="NAME")
    export.add_argument("--cursor", action="store_true", help="also mirror the standards into .cursor/rules")
    export.add_argument("--dry-run", action="store_true")
    export.add_argument("-y", "--yes", action="store_true")
    export.set_defaults(func=cmd_export)

    sync = subparsers.add_parser("sync", help="re-export every registered project")
    sync.add_argument("--dry-run", action="store_true")
    sync.set_defaults(func=cmd_sync)

    status = subparsers.add_parser("status", help="drift, feedback and pending lessons per project")
    status.set_defaults(func=cmd_status)

    remove = subparsers.add_parser("remove", help="remove mast's managed files and hooks from a project")
    remove.add_argument("dir")
    remove.add_argument("--purge", action="store_true", help="also delete learning data and the project config")
    remove.add_argument("-y", "--yes", action="store_true")
    remove.set_defaults(func=cmd_remove)

    doctor = subparsers.add_parser("doctor", help="validate a project's setup and self-test its hooks")
    doctor.add_argument("dir", nargs="?")
    doctor.set_defaults(func=cmd_doctor)

    standard = subparsers.add_parser("standard", help="create and manage private standards")
    standard_subs = standard.add_subparsers(dest="standard_command", metavar="<subcommand>")
    standard_init = standard_subs.add_parser("init", help="scaffold and register a new standard repo")
    standard_init.add_argument("dir")
    standard_init.add_argument("--name")
    standard_init.add_argument("--description")
    standard_add = standard_subs.add_parser("add", help="register an existing standard")
    standard_add.add_argument("dir")
    standard_remove = standard_subs.add_parser("remove", help="unregister a standard")
    standard_remove.add_argument("name")
    standard_check = standard_subs.add_parser("check", help="validate a standard (use in its CI)")
    standard_check.add_argument("dir", nargs="?")
    standard_subs.add_parser("list", help="registered standards")
    standard.set_defaults(func=cmd_standard)

    leak = subparsers.add_parser("leakcheck", help="scan what git would publish for secrets and private terms")
    leak.add_argument("dir", nargs="?")
    leak.add_argument("--term", metavar="TEXT", help="an extra private term to look for")
    leak.set_defaults(func=cmd_leakcheck)

    learn = subparsers.add_parser("learn", help="wins, grievances, analyzer and review stats, recommendations")
    learn.add_argument("--days", type=int, default=30)
    learn.add_argument("--distill", action="store_true", help="turn recent praise/corrections into candidates via `claude -p`")
    learn.set_defaults(func=cmd_learn)

    promote_parser = subparsers.add_parser("promote", help="turn a lesson candidate into a standard")
    promote_parser.add_argument("id")
    promote_parser.add_argument("--scope", choices=("global", "project"))
    promote_parser.add_argument("--pack", help="target pack for a global promotion (default: the standard's defaultPack, else core)")
    promote_parser.add_argument("--sync", action="store_true", help="run `mast sync` afterwards")
    promote_parser.set_defaults(func=cmd_promote)

    reject_parser = subparsers.add_parser("reject", help="dismiss a lesson candidate")
    reject_parser.add_argument("id")
    reject_parser.set_defaults(func=cmd_reject)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (MastError, ValueError) as exc:
        # ValueError is how the analyzer reports an unknown check, category or prefix.
        print(f"{c.red('mast:')} {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:  # `mast check | head`
        return 0
