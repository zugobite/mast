#!/usr/bin/env python3
"""PostToolUse: analyze the file just edited, surface `warn` rules, format, lint, nudge commits.

Also records the reviewer agent's `REVIEW_RESULT` verdict for the end-of-turn review gate.
"""

from __future__ import annotations

import json
import sys

from lib import (
    PROJECT_DIR,
    active_rules,
    add_context,
    added_content,
    analysis_config,
    analyze_project_files,
    blocking_findings,
    commit_settings,
    diff_lines,
    emit,
    format_hits,
    git_head,
    is_inside,
    is_python,
    load_config,
    load_state,
    log_event,
    matches_any,
    read_input,
    render_compact,
    run_command,
    save_state,
    scan,
    tail,
    to_rel,
    uncommitted_files,
)


def collect_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in collect_strings(item)]
    if isinstance(value, dict):
        return [s for item in value.values() for s in collect_strings(item)]
    return []


def parse_review(text: str) -> dict | None:
    """The JSON object after the last REVIEW_RESULT marker (brace-balanced, string-aware)."""
    at = text.rfind("REVIEW_RESULT")
    if at == -1:
        return None
    start = text.find("{", at)
    if start == -1:
        return None
    depth = 0
    in_string = False
    index = start
    while index < len(text):
        char = text[index]
        if in_string:
            if char == "\\":
                index += 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : index + 1])
                except ValueError:
                    return None
        index += 1
    return None


def handle_review(payload: dict, config: dict) -> None:
    tool_input = payload.get("tool_input") or {}
    session = payload.get("session_id")
    review_config = config.get("review") or {}
    if tool_input.get("subagent_type") != (review_config.get("agent") or "reviewer"):
        return
    state = load_state(session)
    result = parse_review("\n".join(collect_strings(payload.get("tool_response"))))
    if not result:
        save_state(session, {**state, "review": {"verdict": "unknown", "editSeq": state.get("editSeq", 0)}})
        add_context(
            "PostToolUse",
            "[mast] The reviewer did not end with a REVIEW_RESULT line, so the review was not recorded. "
            "Re-run it and make sure it ends with that line.",
        )
        return

    findings = result.get("findings") if isinstance(result.get("findings"), list) else []
    block_on = review_config.get("blockOn") or ["high", "medium"]
    count = lambda severity: sum(1 for f in findings if f.get("severity") == severity)  # noqa: E731
    blocking = [f for f in findings if f.get("severity") in block_on]
    verdict = "changes_requested" if blocking else "pass"
    save_state(
        session,
        {
            **state,
            "review": {
                "verdict": verdict,
                "editSeq": state.get("editSeq", 0),
                "high": count("high"),
                "medium": count("medium"),
                "low": count("low"),
            },
        },
    )

    log_event("review", session=session, verdict=verdict, reviewerVerdict=result.get("verdict"), findings=len(findings))
    for finding in findings:
        log_event(
            "review_finding",
            session=session,
            severity=finding.get("severity"),
            category=finding.get("category"),
            rule=finding.get("rule"),
            file=finding.get("file"),
            summary=str(finding.get("summary") or "")[:200],
        )
    for win in result.get("wins") if isinstance(result.get("wins"), list) else []:
        log_event("review_win", session=session, summary=str(win)[:200])

    add_context(
        "PostToolUse",
        "[mast] Review recorded: pass."
        if verdict == "pass"
        else f"[mast] Review recorded: changes requested — {len(blocking)} blocking finding(s). Fix them, then re-run the reviewer.",
    )


def main() -> None:
    payload = read_input()
    config = load_config()
    if config.get("enabled") is False:
        return

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    session = payload.get("session_id")

    if tool in ("Agent", "Task"):
        handle_review(payload, config)
        return

    rel = to_rel(tool_input.get("file_path") or tool_input.get("notebook_path"))
    if not is_inside(rel):
        return

    state = load_state(session)
    if rel not in state["edited"]:
        state["edited"].append(rel)
    state["editSeq"] = state.get("editSeq", 0) + 1
    # The turn's starting commit: review measures from here, so checkpoint commits never hide work.
    if "turnBase" not in state:
        state["turnBase"] = git_head()
    save_state(session, state)

    notes: list[str] = []
    must_fix = False

    warnings = [hit for hit in scan(rel, added_content(tool, tool_input), active_rules(config)) if hit["rule"]["severity"] != "block"]
    for hit in warnings:
        log_event("violation", session=session, rule=hit["rule"]["id"], severity="warn", file=rel)
    if warnings:
        notes.append(f"Standards warnings in {rel} — fix before finishing:\n{format_hits(warnings)}")

    exists = (PROJECT_DIR / rel).exists()
    applies = lambda step: bool(step and step.get("command")) and exists and matches_any(rel, step.get("globs"))  # noqa: E731

    if applies(config.get("format")):
        run_command(config["format"]["command"], rel, int(config["format"].get("timeoutSec", 20)) * 1000)

    # Analyze after formatting, so line numbers match the file on disk.
    if exists and is_python(rel):
        findings = analyze_project_files([rel], config)
        for finding in findings:
            log_event("analysis_finding", session=session, check=finding.check, severity=finding.severity, file=rel)
        blocking = blocking_findings(findings, config, "blockOn")
        if blocking:
            must_fix = True
            notes.append(
                f"The analyzer found {len(blocking)} issue(s) in {rel} that must not be left in:\n"
                f"{render_compact(blocking, limit=8)}"
            )
        other = [f for f in findings if f not in blocking]
        if other:
            notes.append(
                f"Also worth fixing in {rel} while you are here:\n{render_compact(other, limit=6, with_fix=False)}"
            )

    if applies(config.get("lint")):
        result = run_command(config["lint"]["command"], rel, int(config["lint"].get("timeoutSec", 30)) * 1000)
        if result["status"] != 0 and not result["timed_out"]:
            must_fix = True
            log_event("lint_fail", session=session, file=rel)
            notes.append(f"Lint failed for {rel}:\n{tail(result['output'], 40)}")

    # Mid-work checkpoint: once enough of Claude's own work is uncommitted, remind it once per commit.
    commits = commit_settings(config)
    if commits.get("enabled") is not False and commits.get("checkpointLines", 0) > 0:
        head = git_head()
        if head != state.get("checkpointHead"):
            state["checkpointHead"] = head
            state["checkpointReminded"] = False
        if not state.get("checkpointReminded"):
            pending = uncommitted_files(state["edited"]) or []
            lines = diff_lines(pending, "HEAD") if pending else 0
            if lines >= commits["checkpointLines"]:
                state["checkpointReminded"] = True
                notes.append(
                    f"Checkpoint: {lines} uncommitted lines across {len(pending)} file(s) you changed. Once this step is "
                    "coherent (it imports and its tests pass), make a checkpoint commit — a Conventional Commit staging "
                    "only your files (`/checkpoint`) — so the work can't be lost."
                )
        save_state(session, state)

    if not notes:
        return
    body = "\n\n".join(notes)
    # "block" on PostToolUse doesn't undo the edit — it feeds the reason back so Claude fixes it now.
    if must_fix:
        emit({"decision": "block", "reason": f"[mast]\n{body}"})
    else:
        add_context("PostToolUse", f"[mast]\n{body}")


if __name__ == "__main__":
    main()
