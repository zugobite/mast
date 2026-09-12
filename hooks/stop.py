#!/usr/bin/env python3
"""Stop: the quality gates that run before Claude hands a turn back.

Once per turn: changelog and custom stop checks. Every round: the analyzer over the files
changed this turn, the tests related to them, and a reviewer pass for substantial changes.
Finally, Claude's own uncommitted work must be committed. Capped by qa.maxLoops.
"""

from __future__ import annotations

import sys

from lib import (
    PROJECT_DIR,
    PY_GLOBS,
    analyze_project_files,
    blocking_findings,
    commit_settings,
    current_branch,
    diff_lines,
    emit,
    git,
    load_config,
    load_state,
    log_event,
    matches_any,
    read_input,
    related_test_files,
    render_compact,
    run_command,
    save_state,
    shell_quote,
    tail,
    uncommitted_files,
)


def finish_turn(session: str | None, state: dict, edited: list[str], loops: int, gated: bool) -> None:
    if edited:
        base = state.get("turnBase")
        log = git(["log", "--format=%s", f"{base}..HEAD"]) if base else None
        subjects = [s for s in (log.stdout.splitlines() if log and log.returncode == 0 else []) if s]
        for subject in subjects:
            commit_type = subject.split(":", 1)[0].split("(", 1)[0].strip() or "other"
            log_event("commit", session=session, commitType=commit_type)
        log_event("turn", session=session, files=len(edited), gated=gated, loops=loops, commits=len(subjects))
    rest = {k: v for k, v in state.items() if k != "turnBase"}
    save_state(session, {**rest, "edited": [], "loops": 0, "review": None})
    sys.exit(0)


def main() -> None:
    payload = read_input()
    config = load_config()
    if config.get("enabled") is False:
        return

    session = payload.get("session_id")
    state = load_state(session)
    edited = state.get("edited") or []
    qa = {"maxLoops": 3, **(config.get("qa") or {})}
    review = {"minChangedLines": 30, "blockOn": ["high", "medium"], "agent": "reviewer", **(config.get("review") or {})}
    commits = commit_settings(config)
    loops = state.get("loops", 0)

    if not edited:
        finish_turn(session, state, edited, loops, False)
    if loops >= qa["maxLoops"]:
        log_event("gate_giveup", session=session, loops=loops)
        finish_turn(session, state, edited, loops, True)

    code = [f for f in edited if (PROJECT_DIR / f).exists() and matches_any(f, qa.get("codeGlobs") or PY_GLOBS)]
    problems: list[dict] = []

    if loops == 0 and not payload.get("stop_hook_active"):
        changelog = config.get("changelog") or {}
        if changelog.get("file") and changelog.get("requiredFor"):
            code_edits = [f for f in edited if f != changelog["file"] and matches_any(f, changelog["requiredFor"])]
            if code_edits and changelog["file"] not in edited:
                sample = ", ".join(code_edits[:3]) + (", …" if len(code_edits) > 3 else "")
                problems.append(
                    {
                        "gate": "changelog",
                        "text": (
                            f"Changelog: you changed {len(code_edits)} code file(s) ({sample}) without updating "
                            f"{changelog['file']} under \"## [Unreleased]\". Add a user-facing bullet, or tell the user why none is needed."
                        ),
                    }
                )

        for check in config.get("stopChecks") or []:
            if not check.get("command") or check.get("enabled") is False:
                continue
            if check.get("when") and not any(matches_any(f, check["when"]) for f in edited):
                continue
            result = run_command(check["command"], None, int(check.get("timeoutSec", 120)) * 1000)
            if result["status"] != 0:
                name = check.get("name") or "Check"
                problems.append(
                    {
                        "gate": check.get("name") or check["command"],
                        "text": f"{name} failed (`{check['command']}`){' — timed out' if result['timed_out'] else ''}:\n{tail(result['output'], 40)}",
                    }
                )

    # The analyzer, over everything changed this turn.
    if code:
        findings = analyze_project_files(code, config)
        gating = blocking_findings(findings, config, "gateOn")
        log_event("analysis_run", session=session, files=len(code), findings=len(findings), gating=len(gating), loop=loops)
        if gating:
            problems.append(
                {
                    "gate": "analysis",
                    "text": (
                        f"Analyzer: {len(gating)} issue(s) in the code you changed must be fixed before finishing:\n"
                        f"{render_compact(gating, limit=10)}\n"
                        "Run `mast check <paths>` to see them with context, `mast explain <check-id>` for the reasoning. "
                        "If a finding is genuinely wrong here, explain that to the user instead of ignoring it."
                    ),
                }
            )

    related = qa.get("relatedTests")
    if qa.get("enabled") is not False and related and related.get("command"):
        targets = [f for f in code if matches_any(f, related.get("globs") or PY_GLOBS)]
        if related.get("mapToTests"):
            targets = related_test_files(targets, config)
        if targets:
            result = run_command(related["command"], None, int(related.get("timeoutSec", 240)) * 1000, targets)
            passed = result["status"] == 0
            log_event("qa_run", session=session, **{"pass": passed}, files=len(targets), loop=loops)
            if not passed:
                problems.append(
                    {
                        "gate": "qa",
                        "text": (
                            f"QA: the tests covering your changes failed{' (timed out)' if result['timed_out'] else ''}:\n"
                            f"{tail(result['output'], 40)}\nFix the cause (change the test only if the test is wrong) — they run again when you finish."
                        ),
                    }
                )
        elif code:
            problems.append(
                {
                    "gate": "qa",
                    "text": (
                        f"QA: no test file covers {', '.join(code[:3])}{'…' if len(code) > 3 else ''}. Add tests that fail "
                        "without your change (named `test_<module>.py` so they are found), or tell the user why none are needed."
                    ),
                }
            )

    if review.get("enabled") is not False and code:
        lines = diff_lines(code, state.get("turnBase") or "HEAD")
        if lines >= review["minChangedLines"]:
            base = state.get("turnBase")
            since = f" — tell it to review everything since base commit {base[:12]}" if base else ""
            launch = f'launch the `{review["agent"]}` agent (Agent tool, subagent_type "{review["agent"]}") on your changes{since}'
            recorded = state.get("review")
            text = None
            if not recorded:
                text = (
                    f"Review: you changed {lines} lines of Python — {launch} before finishing. "
                    f"Fix every {'/'.join(review['blockOn'])} finding and re-run it until the verdict is pass."
                )
            elif recorded.get("verdict") == "unknown":
                text = f"Review: the last review returned no REVIEW_RESULT line — {launch} again."
            elif recorded.get("verdict") != "pass":
                if recorded.get("editSeq") == state.get("editSeq", 0):
                    text = (
                        f"Review: the reviewer requested changes ({recorded.get('high', 0)} high, {recorded.get('medium', 0)} medium) "
                        "and nothing has been edited since. Fix them and re-run the reviewer — or explain to the user why a finding is wrong."
                    )
                else:
                    text = f"Review: you edited code after the reviewer requested changes — {launch} again to confirm the fixes."
            if text:
                problems.append({"gate": "review", "text": text})

    if not problems and commits.get("enabled") is not False and commits.get("commitOnTurnEnd") is not False:
        pending = uncommitted_files(edited)
        if pending:
            branch = current_branch()
            lines = [
                f"Commit checkpoint: {len(pending)} file(s) you changed this turn are uncommitted.",
                (
                    f'You\'re on protected branch "{branch}" — first run: git switch -c <type>/<short-description>'
                    if branch and branch in commits["protectedBranches"]
                    else None
                ),
                "Commit them as Conventional Commits — one logical change per commit, staging only these paths:",
                f"  git add -- {' '.join(shell_quote(f) for f in pending)}",
                '  git commit -m "<type>(<scope>): <imperative summary>"',
                "If a file also holds the user's unrelated changes, ask before committing it. Don't push.",
            ]
            problems.append({"gate": "commit", "text": "\n".join(l for l in lines if l)})

    if not problems:
        finish_turn(session, state, edited, loops, False)

    save_state(session, {**state, "loops": loops + 1})
    for problem in problems:
        log_event("gate_block", session=session, gate=problem["gate"], loop=loops)
    body = "\n\n".join(problem["text"] for problem in problems)
    emit({"decision": "block", "reason": f"[mast] Before finishing (check {loops + 1}/{qa['maxLoops']}):\n\n{body}"})


if __name__ == "__main__":
    main()
