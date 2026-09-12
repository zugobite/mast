#!/usr/bin/env python3
"""PreToolUse: refuse writes that break hard rules, and guard dangerous shell commands.

Denied before anything happens: `block` text rules, high-severity analyzer findings in a
full-file write, protected paths, destructive or publishing shell commands, commits to a
protected branch and non-Conventional commit headers.
"""

from __future__ import annotations

import re
import sys

from lib import (
    active_rules,
    added_content,
    analyze_written_source,
    blocking_findings,
    commit_settings,
    current_branch,
    emit,
    format_hits,
    is_inside,
    is_python,
    load_config,
    log_event,
    matches_any,
    read_input,
    render_compact,
    scan,
    to_rel,
)

BASH_GUARDS = [
    {
        "id": "bash/force-push",
        "pattern": r"git\s+push\b.*\s(--force(?!-with-lease)|-f)(\s|$)",
        "message": "Force-push is blocked. Use --force-with-lease on your own branch, or ask the user to do it.",
    },
    {
        "id": "bash/push-main",
        "pattern": r"git\s+push\b[^|;&]*(\s|:)(main|master)(\s|$|[;&|])",
        "message": "Pushing directly to main/master is blocked — push a branch and open a PR.",
    },
    {
        "id": "bash/no-verify",
        "pattern": r"git\s+(commit|push)\b.*--no-verify",
        "message": "Do not bypass git hooks with --no-verify — fix what the hook reports.",
    },
    {
        "id": "bash/rm-root",
        "pattern": r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(/|~|\$HOME|\.)\s*($|[;&|])",
        "message": "Refusing recursive delete of a root, home or project directory.",
    },
    {
        "id": "bash/print-env",
        "pattern": r"\b(cat|less|more|head|tail|bat)\s+[^|;&]*\.env(\.(?!example)[\w.-]+)?(\s|$)",
        "message": "Do not print .env files — they contain secrets. Ask the user for the specific non-secret value.",
    },
    {
        "id": "git/stage-all",
        "pattern": r"\bgit\s+(add\s+(-A|--all|-u|--update|\.|:/)(\s|$)|commit(\s+--?[a-zA-Z][a-zA-Z-]*)*?\s+(-[a-zA-Z]*a[a-zA-Z]*|--all)(\s|$))",
        "message": (
            "Stage only the files you changed for this task (git add -- <paths>) — sweeping everything can commit "
            "the user's unrelated work or secrets."
        ),
    },
    {
        "id": "python/publish-package",
        "pattern": r"\b(twine\s+upload|poetry\s+publish|flit\s+publish|uv\s+publish|python\s+setup\.py\s+(upload|register))\b",
        "message": "Publishing a package is the user's call — build the artifact and ask them to release it.",
    },
    {
        "id": "python/destructive-db",
        "pattern": r"\b(manage\.py\s+(flush|sqlflush|reset_db)|dropdb\b|DROP\s+(DATABASE|TABLE)\b|alembic\s+downgrade\s+base)\b",
        "message": "Destroying a database or table is never part of a code change — ask the user to run it themselves.",
    },
    {
        "id": "python/pip-system",
        "pattern": r"\b(sudo\s+(-H\s+)?(pip|pip3)\s+install|pip\s+install\s+[^|;&]*--break-system-packages)\b",
        "message": "Do not install into the system Python. Use the project's virtualenv or `uv`/`poetry` so the change is reproducible.",
    },
    {
        "id": "bash/chmod-world-write",
        "pattern": r"\bchmod\s+(-[a-zA-Z]+\s+)*[0-7]?[0-7][0-7][2367]\b",
        "message": "World-writable permissions let any local user modify the file — use 644/755.",
    },
]

PROTECTED = [
    {
        "globs": [
            ".claude/standards/packs/**",
            ".claude/standards/hooks/**",
            ".claude/standards/rules.json",
            ".claude/standards/manifest.json",
        ],
        "message": (
            "managed by mast — change it in its source repo (core or standard; see .claude/standards/manifest.json) "
            "and run `mast sync`. Project-only rules belong in .claude/standards/local-rules.json, local-lessons.md or local-wins.md."
        ),
    },
    {
        "globs": [".env", ".env.*"],
        "exclude": [".env.example", ".env.sample", ".env.template"],
        "message": "agents must not write .env files. Tell the user which variable to set instead.",
    },
]

CONVENTIONAL_EXEMPT = re.compile(r"^(Merge |Revert \"|fixup! |squash! |amend! )")


def deny(reason: str) -> None:
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"[mast] {reason}",
            }
        }
    )
    sys.exit(0)


def commit_subject(command: str) -> str | None:
    """First line of the message in `git commit -m …` or heredoc form; None when unreadable."""
    heredoc = re.search(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n([\s\S]*?)\n\s*\1\b", command)
    message = heredoc.group(2) if heredoc else None
    if message is None:
        match = re.search(r"\s(?:-[a-zA-Z]*m|--message)(?:=|\s+)(\"(?:[^\"\\]|\\.)*\"|'[^']*')", command)
        if not match:
            return None
        message = match.group(1)[1:-1].replace("\\n", "\n")
    if "$(" in message:
        return None
    return next((line.strip() for line in message.splitlines() if line.strip()), None)


def header_problem(subject: str, commits: dict) -> str | None:
    if CONVENTIONAL_EXEMPT.match(subject):
        return None
    types = "|".join(re.escape(t) for t in commits["types"])
    if not re.match(rf"^({types})(\([^()\s][^()]*\))?!?: \S", subject):
        return (
            f'"{subject[:80]}" isn\'t a Conventional Commit header. Use "<type>(<optional scope>): <imperative summary>" '
            f"with type one of {', '.join(commits['types'])} — e.g. \"feat(users): add bulk archive\"."
        )
    if len(subject) > commits["maxHeaderLength"]:
        return f"The commit header is {len(subject)} characters — keep it within {commits['maxHeaderLength']} and move detail into the body."
    if subject.endswith("."):
        return "Drop the trailing period from the commit header."
    return None


def commit_violation(command: str, config: dict, disabled: set) -> dict | None:
    if not re.search(r"\bgit\s+commit\b", command):
        return None
    commits = commit_settings(config)
    if commits.get("enabled") is False:
        return None
    if "git/protected-branch" not in disabled:
        branch = current_branch()
        if branch and branch in commits["protectedBranches"]:
            return {
                "id": "git/protected-branch",
                "message": (
                    f'You\'re on "{branch}", which is protected. Create a branch first: '
                    "git switch -c <type>/<short-description> (e.g. feat/bulk-archive)."
                ),
            }
    if "git/conventional-commit" not in disabled:
        subject = commit_subject(command)
        problem = header_problem(subject, commits) if subject else None
        if problem:
            return {"id": "git/conventional-commit", "message": problem}
    return None


def main() -> None:
    payload = read_input()
    config = load_config()
    if config.get("enabled") is False:
        return

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    session = payload.get("session_id")
    disabled = set(config.get("disabledRules") or ())

    if tool == "Bash":
        command = str(tool_input.get("command") or "")
        for guard in [*BASH_GUARDS, *(config.get("bashGuards") or [])]:
            if guard.get("id") in disabled or not guard.get("pattern"):
                continue
            try:
                matched = re.search(guard["pattern"], command, re.IGNORECASE if "i" in str(guard.get("flags") or "") else 0)
            except re.error:
                continue  # a project's own broken guard pattern must not block every command
            if matched:
                log_event("violation", session=session, rule=guard["id"], severity="block", command=command[:200])
                deny(f"{guard['message']} ({guard['id']})")
        violation = commit_violation(command, config, disabled)
        if violation:
            log_event("violation", session=session, rule=violation["id"], severity="block", command=command[:200])
            deny(f"{violation['message']} ({violation['id']})")
        return

    rel = to_rel(tool_input.get("file_path") or tool_input.get("notebook_path"))
    if not is_inside(rel):
        return

    for protected in [*PROTECTED, *(config.get("protectedPaths") or [])]:
        if matches_any(rel, protected.get("globs")) and not (
            protected.get("exclude") and matches_any(rel, protected["exclude"])
        ):
            log_event("violation", session=session, rule="protected-path", severity="block", file=rel)
            deny(f"{rel} is protected: {protected['message']}")

    content = added_content(tool, tool_input)
    blocking = [hit for hit in scan(rel, content, active_rules(config)) if hit["rule"]["severity"] == "block"]
    if blocking:
        for hit in blocking:
            log_event("violation", session=session, rule=hit["rule"]["id"], severity="block", file=rel)
        deny(
            f"Write to {rel} blocked by project standards:\n{format_hits(blocking)}\n"
            "Fix the content and retry. If the rule is wrong for this case, explain that to the user instead of working around it."
        )

    # A `Write` carries the whole file, so it can be analyzed before it lands.
    if tool == "Write" and is_python(rel):
        findings = blocking_findings(analyze_written_source(rel, content, config), config, "blockOn")
        if findings:
            for finding in findings:
                log_event("analysis_finding", session=session, check=finding.check, severity=finding.severity, file=rel, blocked=True)
            deny(
                f"Write to {rel} blocked — the analyzer found {len(findings)} issue(s) that must not land:\n"
                f"{render_compact(findings, limit=10)}\n"
                "Fix them in the content you are writing and retry. `mast explain <check-id>` has the reasoning. "
                "If a finding is wrong for this case, say so to the user rather than working around it."
            )


if __name__ == "__main__":
    main()
