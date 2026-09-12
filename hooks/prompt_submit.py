#!/usr/bin/env python3
"""UserPromptSubmit: notice praise and corrections, log them, and nudge Claude to record lessons."""

from __future__ import annotations

import re

from lib import add_context, load_config, log_event, read_input

# Strong phrases count anywhere; generic words ("no", "stop", "don't") only when they open the
# message, so ordinary instructions like "…and then stop." are not logged as corrections.
STRONG_CORRECTION = re.compile(
    r"\b(i (already )?(told|said|asked) you|you (forgot|missed|broke|ignored|didn'?t)|why did you|"
    r"that'?s not (what|how|right)|not what i (asked|wanted|meant)|should(n'?t| not) have|again[?!])",
    re.IGNORECASE,
)
LEADING_CORRECTION = re.compile(
    r"^(no\b|nope\b|wrong\b|stop\b|don'?t\b|do not\b|never\b|actually\b|instead\b|revert\b|undo\b)", re.IGNORECASE
)
STRONG_PRAISE = re.compile(
    r"\b(great|excellent|amazing|awesome|fantastic|brilliant|perfect|stellar) (work|job)\b|"
    r"\bexactly what i (wanted|needed|meant|asked for)\b|\b(love|loving) (it|this|that)\b|"
    r"\bthis is (perfect|excellent|amazing|brilliant|exactly)\b|\bnailed it\b",
    re.IGNORECASE,
)
LEADING_PRAISE = re.compile(
    r"^(perfect|great|awesome|amazing|excellent|brilliant|beautiful|fantastic|exactly|spot on|nice|love it)\b",
    re.IGNORECASE,
)


def is_likely_correction(text: str) -> bool:
    return bool(STRONG_CORRECTION.search(text) or LEADING_CORRECTION.match(text.strip()))


def is_likely_praise(text: str) -> bool:
    return bool(STRONG_PRAISE.search(text) or LEADING_PRAISE.match(text.strip()))


def main() -> None:
    payload = read_input()
    config = load_config()
    if config.get("enabled") is False or (config.get("learning") or {}).get("captureCorrections") is False:
        return

    prompt = str(payload.get("prompt") or "").strip()
    if not prompt or prompt.startswith("/") or len(prompt) > 2000:
        return

    session = payload.get("session_id")
    notes: list[str] = []

    if is_likely_praise(prompt):
        log_event("praise", session=session, prompt=prompt[:500])
        notes.append(
            "The user is pleased with the recent work. If a specific, reusable approach earned it (a structure, an API "
            'shape, a testing pattern), record it as a win — format in .claude/commands/win.md, "source":"praise", with '
            'the exemplary file(s) in "examples" — so it is reproduced in future sessions. Do not record generic praise.'
        )
    if is_likely_correction(prompt):
        log_event("correction", session=session, prompt=prompt[:500])
        notes.append(
            "This message may be a correction. Address it first. If it reflects a durable preference (beyond this task), "
            'also record it as a grievance — format in .claude/commands/grievance.md, "source":"correction". '
            "If it is a one-off, ignore this note."
        )

    if notes:
        add_context("UserPromptSubmit", f"[mast] {chr(10).join(notes)}")


if __name__ == "__main__":
    main()
