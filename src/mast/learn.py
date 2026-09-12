"""The learning loop, central side: aggregate what happened across projects, promote lessons."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .common import (
    DECISIONS,
    ROOT,
    STATE,
    MastError,
    list_packs,
    load_registry,
    load_standard,
    pack_dir,
    pack_rules,
    read_json,
    read_jsonl,
    registered_standards,
    tildify,
    write_json,
)
from .standard import grievances_header, wins_header

CENTRAL_CANDIDATES = STATE / "candidates.jsonl"
DISTILLED = STATE / "distilled.json"
REPORT = STATE / "learning-report.md"


def _learn_file(directory: str | Path, name: str) -> Path:
    return Path(directory) / ".claude" / "standards" / "learning" / name


def _hash_id(text: str) -> str:
    return hashlib.sha1(text.lower().strip().encode(), usedforsecurity=False).hexdigest()[:8]


def _pct(part: int, whole: int) -> int:
    return round(part / whole * 100) if whole else 0


def _parse_ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def collect_candidates(registry: dict | None = None) -> list[dict]:
    registry = registry or load_registry()
    decisions = read_json(DECISIONS, {}) or {}
    by_id: dict[str, dict] = {}

    def add(candidate: dict, directory: str | None) -> None:
        if not candidate.get("id") or not candidate.get("lesson"):
            return
        existing = by_id.get(candidate["id"])
        if existing is not None:
            if directory:
                existing["projects"].add(directory)
            return
        by_id[candidate["id"]] = {
            **candidate,
            "kind": "win" if candidate.get("kind") == "win" else "grievance",
            "projectDir": directory,
            "projects": {directory} if directory else set(),
        }

    for directory in registry["projects"]:
        for candidate in read_jsonl(_learn_file(directory, "candidates.jsonl")):
            add(candidate, directory)
    for candidate in read_jsonl(CENTRAL_CANDIDATES):
        add(candidate, candidate.get("projectDir"))

    return [
        {**candidate, "projects": sorted(candidate["projects"])}
        for candidate in by_id.values()
        if candidate["id"] not in decisions
    ]


def collect_events(registry: dict, days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict] = []
    for directory in registry["projects"]:
        for event in read_jsonl(_learn_file(directory, "events.jsonl")):
            timestamp = _parse_ts(event.get("ts"))
            if timestamp and timestamp >= cutoff:
                out.append({**event, "projectDir": directory})
    return out


@dataclass
class Analysis:
    days: int
    projects: list[str] = field(default_factory=list)
    events: int = 0
    rule_stats: dict[str, dict] = field(default_factory=dict)
    finding_stats: Counter = field(default_factory=Counter)
    gates: Counter = field(default_factory=Counter)
    turns: int = 0
    gated_turns: int = 0
    corrections: list[dict] = field(default_factory=list)
    praise: list[dict] = field(default_factory=list)
    lint_fails: int = 0
    candidates: list[dict] = field(default_factory=list)
    quality: dict = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)


def analyze(*, days: int = 30) -> Analysis:
    registry = load_registry()
    events = collect_events(registry, days)
    result = Analysis(days=days, projects=sorted(registry["projects"]), events=len(events))

    for event in events:
        if event["type"] == "violation" and event.get("rule"):
            stats = result.rule_stats.setdefault(event["rule"], {"total": 0, "block": 0, "warn": 0, "projects": set()})
            stats["total"] += 1
            stats["block" if event.get("severity") == "block" else "warn"] += 1
            stats["projects"].add(event["projectDir"])
        elif event["type"] == "analysis_finding" and event.get("check"):
            result.finding_stats[event["check"]] += 1
        elif event["type"] == "gate_block":
            result.gates[event.get("gate", "?")] += 1

    turns = [e for e in events if e["type"] == "turn"]
    result.turns = len(turns)
    result.gated_turns = sum(1 for t in turns if t.get("gated"))
    result.corrections = [e for e in events if e["type"] == "correction"]
    result.praise = [e for e in events if e["type"] == "praise"]
    result.lint_fails = sum(1 for e in events if e["type"] == "lint_fail")
    result.candidates = collect_candidates(registry)

    reviews = [e for e in events if e["type"] == "review"]
    findings = [e for e in events if e["type"] == "review_finding"]
    qa_runs = [e for e in events if e["type"] == "qa_run"]
    result.quality = {
        "reviews": len(reviews),
        "reviewPass": sum(1 for e in reviews if e.get("verdict") == "pass"),
        "findingsByCategory": Counter(e.get("category") for e in findings if e.get("category")).most_common(),
        "findingsByRule": Counter(e.get("rule") for e in findings if e.get("rule")).most_common(),
        "analyzerFindings": result.finding_stats.most_common(),
        "qaRuns": len(qa_runs),
        "qaPass": sum(1 for e in qa_runs if e.get("pass")),
        "giveups": sum(1 for e in events if e["type"] == "gate_giveup"),
        "commits": sum(t.get("commits", 0) for t in turns),
        "turnsWithCommits": sum(1 for t in turns if t.get("commits")),
        "reviewWins": [e for e in events if e["type"] == "review_win"][-5:],
    }

    result.recommendations = _recommend(result)
    return result


def _recommend(a: Analysis) -> list[str]:
    out: list[str] = []
    for rule_id, stats in a.rule_stats.items():
        if stats["warn"] >= 5 and len(stats["projects"]) >= 2:
            out.append(
                f"**{rule_id}** warned {stats['warn']}× across {len(stats['projects'])} projects — Claude keeps ignoring it. "
                "Raise it to `block` or add a clearer example to the standard."
            )
        if stats["block"] >= 3:
            out.append(
                f"**{rule_id}** blocked {stats['block']}× — Claude keeps attempting this. Add explicit guidance "
                "(with a bad/good example) to the standards doc so it stops trying."
            )
    for check_id, count in a.finding_stats.most_common():
        if count >= 5:
            out.append(
                f"The analyzer found **{check_id}** {count}× — write the rule into the standards doc for that area "
                f"(`mast explain {check_id}` has the wording), so Claude stops producing it."
            )
    for gate, count in a.gates.items():
        if a.turns >= 5 and count / a.turns > 0.25:
            out.append(f"Gate **{gate}** fired on {_pct(count, a.turns)}% of editing turns — strengthen that instruction in the standards.")
    if a.lint_fails >= 5:
        out.append(f"Lint failed {a.lint_fails}× on agent edits — capture the most common lint errors as lessons.")
    if len(a.corrections) >= 5:
        out.append(f"{len(a.corrections)} likely corrections logged — run `mast learn --distill` to turn them into grievance candidates.")
    pending_wins = sum(1 for c in a.candidates if c["kind"] == "win")
    if len(a.praise) >= 3 and not pending_wins:
        out.append(
            f"{len(a.praise)} moments of praise but no wins recorded — run `/retro` right after work you love (or `/win`), "
            "so the exemplar files get captured."
        )
    if a.corrections and a.praise:
        total = len(a.praise) + len(a.corrections)
        out.append(f"Feedback ratio: {len(a.praise)} praise vs {len(a.corrections)} corrections ({_pct(len(a.praise), total)}% positive) — track this over time.")
    for candidate in a.candidates:
        if candidate.get("scope") == "project" and len(candidate["projects"]) >= 2:
            out.append(f"Candidate **{candidate['id']}** was recorded as project-scoped in {len(candidate['projects'])} projects — promote it globally.")
    for category, count in a.quality["findingsByCategory"]:
        if count >= 5:
            out.append(
                f"Reviewer flagged **{category}** {count}× — capture the recurring cause as a grievance (`/grievance`) "
                "or tighten the standard so it is caught before review."
            )
    for rule_id, count in a.quality["findingsByRule"]:
        if count >= 3:
            out.append(f"Reviewer cited **{rule_id}** {count}× — the hooks didn't stop it; raise its severity or sharpen its pattern.")
    if a.quality["qaRuns"] >= 5 and a.quality["qaPass"] / a.quality["qaRuns"] < 0.7:
        out.append(
            f"Related tests were red on {100 - _pct(a.quality['qaPass'], a.quality['qaRuns'])}% of turn ends — "
            "have Claude run `/qa` before it says it is done."
        )
    if a.quality["giveups"]:
        out.append(f"{a.quality['giveups']} turn(s) hit the QA/review loop limit — inspect those sessions; a gate or standard may be wrong.")

    if len(a.projects) >= 2 and a.days >= 30 and a.turns >= 20:
        ids: list[str] = []
        for standard in [None, *registered_standards()]:
            for pack in list_packs(standard):
                if standard and pack.origin != standard.name:
                    continue
                ids += [r["id"] for r in pack_rules(pack.name, standard) if r.get("id")]
        silent = [rule_id for rule_id in dict.fromkeys(ids) if rule_id not in a.rule_stats]
        if silent:
            out.append(f"No hits in {a.days} days: {', '.join(silent)} — keep if they are guardrails, prune if they are noise.")
    return out


def render_report(a: Analysis) -> str:
    quality = a.quality
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    out = [
        "# mast learning report",
        "",
        f"_Generated {generated} · last {a.days} days · {len(a.projects)} projects · {a.events} events_",
        "",
        "## Recommendations",
        "",
        *( [f"- {r}" for r in a.recommendations] or ["- Nothing actionable yet — keep working; the loop needs data."]),
        "",
    ]
    escape = lambda text: str(text).replace("|", "\\|")  # noqa: E731
    for kind, title in (("win", "Pending wins — reproduce"), ("grievance", "Pending grievances — never repeat")):
        rows = [c for c in a.candidates if c["kind"] == kind]
        out += [f"## {title}", ""]
        if not rows:
            out += ["None.", ""]
            continue
        out += ["| id | scope | source | seen in | lesson | extra |", "| --- | --- | --- | --- | --- | --- |"]
        for row in rows:
            seen = ", ".join(Path(p).name for p in row["projects"]) or "—"
            extra = f"rule {row['rule']['id']}" if row.get("rule") else (f"exemplars: {', '.join(row.get('examples') or [])}" if row.get("examples") else "")
            out.append(f"| `{row['id']}` | {row.get('scope', 'global')} | {row.get('source', kind)} | {seen} | {escape(row['lesson'])} | {escape(extra)} |")
        out.append("")
    out.append("Promote with `mast promote <id> [--scope global|project] [--pack core|web|…]`, dismiss with `mast reject <id>`, then `mast sync`.")

    out += ["", "## Rule hits", ""]
    rules = sorted(a.rule_stats.items(), key=lambda kv: -kv[1]["total"])
    if rules:
        out += ["| rule | total | blocked | warned | projects |", "| --- | --- | --- | --- | --- |"]
        out += [f"| {rule_id} | {s['total']} | {s['block']} | {s['warn']} | {len(s['projects'])} |" for rule_id, s in rules]
    else:
        out.append("No violations recorded.")

    out += ["", "## Analyzer findings", ""]
    if quality["analyzerFindings"]:
        out += ["| check | count |", "| --- | --- |"]
        out += [f"| {check_id} | {count} |" for check_id, count in quality["analyzerFindings"]]
    else:
        out.append("No analyzer findings recorded.")

    out += [
        "",
        "## End-of-turn gates",
        "",
        f"Editing turns: {a.turns} · gated: {a.gated_turns} ({_pct(a.gated_turns, a.turns)}%) · lint failures: {a.lint_fails}",
        *[f"- {gate}: {count}×" for gate, count in a.gates.items()],
        "",
        "## QA & review",
        "",
        f"Reviews: {quality['reviews']} ({_pct(quality['reviewPass'], quality['reviews'])}% pass) · "
        f"related-test runs: {quality['qaRuns']} ({_pct(quality['qaPass'], quality['qaRuns'])}% green) · "
        f"loop limit hit: {quality['giveups']} · commits: {quality['commits']} in {quality['turnsWithCommits']}/{a.turns} editing turns",
        "",
    ]
    if quality["findingsByCategory"]:
        out += ["| finding category | count |", "| --- | --- |"]
        out += [f"| {name} | {count} |" for name, count in quality["findingsByCategory"]]
        out.append("")
    if quality["findingsByRule"]:
        out += [f"Rules cited by the reviewer: {', '.join(f'{k} ({n})' for k, n in quality['findingsByRule'])}", ""]
    if quality["reviewWins"]:
        out += ["Reviewer-noted wins (candidates for `/win`):"]
        out += [f"- `{Path(e['projectDir']).name}` — {e.get('summary', '')}" for e in quality["reviewWins"]]
        out.append("")

    feedback = [{**e, "tag": "praise"} for e in a.praise] + [{**e, "tag": "correction"} for e in a.corrections]
    feedback.sort(key=lambda e: str(e.get("ts")))
    out += ["## Recent feedback", "", f"Praise: {len(a.praise)} · corrections: {len(a.corrections)}", ""]
    out += [
        f"- {'👍' if e['tag'] == 'praise' else '👎'} `{Path(e['projectDir']).name}` — “{' '.join(str(e.get('prompt', '')).split())[:200]}”"
        for e in feedback[-15:]
    ]
    out.append("")
    return "\n".join(out)


DISTILL_PROMPT = [
    "You are improving the Python coding standards of an AI coding agent. Below are developer messages across "
    "several projects, each tagged [praise] (the developer liked the agent's work) or [correction] (the developer corrected it).",
    "Extract only DURABLE, reusable items that should shape future work. Ignore one-off task instructions and generic praise with no specific approach in it.",
    '- From corrections → kind "grievance": an imperative rule that prevents the mistake.',
    '- From praise that names what was good → kind "win": an imperative instruction that reproduces the approach.',
    'Return at most 10 items as JSON Lines: one JSON object per line, no prose, no code fences. Shape: '
    '{"kind":"win"|"grievance","scope":"global"|"project","project":"<project name when scope is project>",'
    '"lesson":"imperative, max 30 words","why":"one line","evidence":"short quote"}.',
    "If nothing is durable, return nothing.",
    "",
    "Messages:",
]


def distill(*, days: int = 30) -> dict:
    """Turn recent praise and corrections into lesson candidates, via the user's own `claude` CLI."""
    registry = load_registry()
    done = read_json(DISTILLED, {}) or {}
    key = lambda event: _hash_id(f"{event['projectDir']}|{event['ts']}")  # noqa: E731
    fresh = [
        event
        for event in collect_events(registry, days)
        if event["type"] in ("correction", "praise") and key(event) not in done
    ][-60:]
    if not fresh:
        return {"added": [], "note": "No new corrections or praise to distill."}
    if not shutil.which("claude"):
        raise MastError("`claude` is not on PATH — `mast learn --distill` uses your own Claude Code CLI.")

    prompt = "\n".join(
        DISTILL_PROMPT
        + [f"[{e['type']}] [{Path(e['projectDir']).name}] {' '.join(str(e.get('prompt', '')).split())}" for e in fresh]
    )
    STATE.mkdir(parents=True, exist_ok=True)
    # Run from STATE so no project's hooks or CLAUDE.md load into the distillation call.
    result = subprocess.run(["claude", "-p", prompt], cwd=str(STATE), capture_output=True, text=True, timeout=240, check=False)
    if result.returncode != 0:
        raise MastError(f"claude -p failed: {(result.stderr or result.stdout).strip() or f'exit {result.returncode}'}")

    by_name = {Path(d).name: d for d in registry["projects"]}
    existing = {c.get("id") for c in read_jsonl(CENTRAL_CANDIDATES)}
    added: list[dict] = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line.strip())
        except ValueError:
            continue  # the model may emit prose between JSON lines; skip it
        if not isinstance(item, dict) or not isinstance(item.get("lesson"), str):
            continue
        scope = "project" if item.get("scope") == "project" else "global"
        candidate = {
            "id": _hash_id(item["lesson"]),
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "distill",
            "kind": "win" if item.get("kind") == "win" else "grievance",
            "scope": scope,
            "lesson": item["lesson"].strip(),
            "why": item.get("why"),
            "evidence": item.get("evidence"),
            "projectDir": by_name.get(item.get("project")) if scope == "project" else None,
        }
        if candidate["id"] in existing:
            continue
        existing.add(candidate["id"])
        with CENTRAL_CANDIDATES.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(candidate) + "\n")
        added.append(candidate)

    for event in fresh:
        done[key(event)] = True
    write_json(DISTILLED, done)
    return {"added": added}


def find_candidate(prefix: str) -> dict:
    if not prefix:
        raise MastError("Pass a candidate id (see `mast learn`).")
    matches = [c for c in collect_candidates() if c["id"].startswith(prefix)]
    if not matches:
        raise MastError(f'No pending candidate matches "{prefix}". Run `mast learn` to list them.')
    if len(matches) > 1:
        raise MastError(f'"{prefix}" is ambiguous: {", ".join(m["id"] for m in matches)}')
    return matches[0]


def _append_lesson(file: Path, candidate: dict, header: str, exemplars: Iterable[str] = ()) -> None:
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(header, encoding="utf-8")
    text = file.read_text(encoding="utf-8")
    if f"lesson:{candidate['id']}" in text:
        return
    why = f" — _{candidate['why']}_" if candidate.get("why") else ""
    refs = f" · exemplar: {', '.join(f'`{e}`' for e in exemplars)}" if exemplars else ""
    line = f"- {candidate['lesson']}{why}{refs} <!-- lesson:{candidate['id']} -->\n"
    file.write_text(text + line if text.endswith("\n") else f"{text}\n{line}", encoding="utf-8")


def _append_rule(file: Path, rule: dict) -> None:
    rules = read_json(file, []) or []
    if any(r.get("id") == rule.get("id") for r in rules):
        return
    write_json(file, [*rules, rule])


def _decide(candidate_id: str, decision: dict) -> None:
    decisions = read_json(DECISIONS, {}) or {}
    decisions[candidate_id] = {**decision, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    write_json(DECISIONS, decisions)


def promote(prefix: str, *, scope: str | None = None, pack: str | None = None) -> dict:
    candidate = find_candidate(prefix)
    final_scope = scope or candidate.get("scope") or "global"
    is_win = candidate["kind"] == "win"
    written: list[str] = []
    target = None

    if final_scope == "global":
        entry = load_registry()["projects"].get(candidate.get("projectDir") or "") or {}
        standard = load_standard(entry.get("standard"))
        pack = pack or (standard.default_pack if standard else None) or "core"
        directory = pack_dir(pack, standard)
        target = (
            {"name": standard.name, "root": standard.root}
            if standard and str(directory).startswith(str(standard.packs_dir))
            else {"name": "core", "root": ROOT}
        )
        exemplars: list[str] = []
        if is_win and candidate.get("projectDir"):
            for rel in candidate.get("examples") or []:
                source = Path(candidate["projectDir"]) / rel
                if not source.exists():
                    continue
                name = f"{candidate['id']}-{Path(rel).name}"
                (directory / "exemplars").mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, directory / "exemplars" / name)
                exemplars.append(f".claude/standards/packs/{pack}/exemplars/{name}")
                written.append(f"{tildify(directory)}/exemplars/{name} (from {Path(candidate['projectDir']).name}/{rel} — review before committing)")
        file = "wins.md" if is_win else "grievances.md"
        header = wins_header(pack) if is_win else grievances_header(pack)
        _append_lesson(directory / file, candidate, header, exemplars)
        written.insert(0, f"{tildify(directory)}/{file}")
        if candidate.get("rule"):
            _append_rule(directory / "rules.json", candidate["rule"])
            written.append(f"{tildify(directory)}/rules.json ({candidate['rule']['id']})")
    else:
        directory = candidate.get("projectDir")
        if not directory:
            raise MastError("This candidate is not tied to a project — use --scope global.")
        standards_dir = Path(directory) / ".claude" / "standards"
        file = "local-wins.md" if is_win else "local-lessons.md"
        header = (
            "# Project wins — reproduce these\n\n<!-- mast:wins -->\n"
            if is_win
            else "# Project grievances & overrides\n\n<!-- mast:grievances -->\n"
        )
        _append_lesson(standards_dir / file, candidate, header, candidate.get("examples") or [])
        written.append(f"{tildify(directory)}/.claude/standards/{file}")
        if candidate.get("rule"):
            _append_rule(standards_dir / "local-rules.json", candidate["rule"])
            written.append(f"{tildify(directory)}/.claude/standards/local-rules.json ({candidate['rule']['id']})")

    _decide(candidate["id"], {"status": "promoted", "scope": final_scope, **({"pack": pack} if final_scope == "global" else {})})
    return {"candidate": candidate, "scope": final_scope, "pack": pack, "written": written, "target": target}


def reject(prefix: str) -> dict:
    candidate = find_candidate(prefix)
    _decide(candidate["id"], {"status": "rejected"})
    return candidate
