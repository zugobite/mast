#!/usr/bin/env python3
"""SessionStart: tell Claude what is active here, and what it keeps getting wrong in this repo."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from datetime import datetime, timezone, timedelta

from lib import (
    REGISTRY,
    STATE_DIR,
    STD_DIR,
    active_rules,
    add_context,
    analysis_config,
    analysis_enabled,
    commit_settings,
    load_config,
    read_events,
    read_input,
    read_json,
    read_jsonl,
)


def prune_state() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
    try:
        for file in STATE_DIR.iterdir():
            if file.is_file() and file.stat().st_mtime < cutoff:
                file.unlink()
    except OSError:
        pass  # no state yet


def count_lessons(file: Path) -> int:
    try:
        return file.read_text(encoding="utf-8").count("<!-- lesson:")
    except OSError:
        return 0


def main() -> None:
    read_input()
    config = load_config()
    if config.get("enabled") is False:
        return
    prune_state()

    manifest = read_json(STD_DIR / "manifest.json", {}) or {}
    window_days = (config.get("learning") or {}).get("windowDays", 30)
    events = read_events(window_days)
    rules = active_rules(config)
    standard = manifest.get("standard") or None

    def tally(event_type: str, key: str) -> list[tuple[str, int]]:
        return Counter(e[key] for e in events if e.get("type") == event_type and e.get(key)).most_common()

    source = f'Standard "{standard["name"]}", profile' if standard else "Profile"
    lines = [
        f'[mast] {source} "{manifest.get("profile", "?")}" (core v{manifest.get("version", "?")}) is active. '
        "Standards are imported via CLAUDE.md; hooks block hard violations, analyze and lint every edit, and gate the end of each turn."
    ]

    if analysis_enabled(config):
        settings = analysis_config(config)
        block_on = "/".join(settings.get("blockOn") or ["high"])
        lines.append(
            f"The analyzer ({len(REGISTRY)} checks: bugs, efficiency, structure, standards, security) runs on every Python file you "
            f"touch and again before the turn ends; {block_on}-severity findings block. Run `mast check <paths>` yourself while you "
            "work and `mast explain <check-id>` when a finding is unclear — do not wait for the hook to tell you."
        )

    violations = tally("violation", "rule")[:5]
    if violations:
        lines.append(f"Recurring standards violations in this repo (last {window_days} days) — actively avoid these:")
        for rule_id, count in violations:
            message = next((r.get("message", "") for r in rules if r.get("id") == rule_id), "")
            lines.append(f"- {rule_id} ({count}×) {message}")

    findings = tally("analysis_finding", "check")[:5]
    if findings:
        described = ", ".join(
            f"{check_id} {count}× ({REGISTRY[check_id].title})" if check_id in REGISTRY else f"{check_id} {count}×"
            for check_id, count in findings
        )
        lines.append(f"The analyzer's most frequent findings in this repo: {described} — write code that does not produce these.")

    gates = tally("gate_block", "gate")
    if gates:
        lines.append(
            f"End-of-turn gates that fired recently: {', '.join(f'{gate} {count}×' for gate, count in gates)}. Handle these before you finish."
        )

    lint_fails = sum(1 for e in events if e.get("type") == "lint_fail")
    if lint_fails >= 3:
        lines.append(f"Lint failed {lint_fails}× on agent edits recently — follow the lint config closely.")

    win_files = [STD_DIR / "local-wins.md"]
    packs_dir = STD_DIR / "packs"
    if packs_dir.is_dir():
        win_files += [pack / "wins.md" for pack in sorted(packs_dir.iterdir()) if pack.is_dir()]
    wins = sum(count_lessons(f) for f in win_files)
    if wins:
        lines.append(
            f"{wins} proven win(s) are recorded in the Wins sections of your standards. Before starting similar work, "
            "reuse those approaches and read their exemplars."
        )

    praise = sum(1 for e in events if e.get("type") == "praise")
    corrections = sum(1 for e in events if e.get("type") == "correction")
    if praise + corrections:
        lines.append(f"Recent feedback in this repo: {praise} praise, {corrections} correction(s).")

    qa = config.get("qa") or {}
    review = config.get("review") or {}
    qa_on = qa.get("enabled") is not False and (qa.get("relatedTests") or {}).get("command")
    review_on = bool(review) and review.get("enabled") is not False
    if qa_on or review_on:
        parts = []
        if qa_on:
            parts.append("the tests covering the files you changed run")
        if review_on:
            parts.append(f"changes of {review.get('minChangedLines', 30)}+ lines need a pass from the `{review.get('agent', 'reviewer')}` agent")
        lines.append(
            f"QA/review loop: before your turn ends, {' and '.join(parts)}. Run `/qa` proactively; `/hunt` for a deep bug hunt; "
            "`/ship` when the work is ready for a PR."
        )

    categories = tally("review_finding", "category")[:3]
    if categories:
        lines.append(
            f"The reviewer's most frequent findings in this repo: {', '.join(f'{name} {count}×' for name, count in categories)} — "
            "get these right before review."
        )

    commits = commit_settings(config)
    if commits.get("enabled") is not False:
        checkpoint = f" and whenever ~{commits['checkpointLines']} lines are uncommitted" if commits.get("checkpointLines", 0) > 0 else ""
        lines.append(
            f"Commit policy: work on a branch (never commit to {'/'.join(commits['protectedBranches'])}); make small Conventional "
            f"Commits of your own files at each coherent, green step{checkpoint}; commit before ending a turn (`/checkpoint`). "
            "Stage explicit paths only. Never push unless the user asks."
        )

    pending = len(read_jsonl(STD_DIR / "learning" / "candidates.jsonl"))
    if pending:
        lines.append(f"{pending} win/grievance candidate(s) recorded locally; the user reviews them with `mast learn`.")

    add_context("SessionStart", "\n".join(lines))


if __name__ == "__main__":
    main()
