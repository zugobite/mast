#!/usr/bin/env python3
"""Append one lesson candidate (a JSON object on stdin) to the project's candidates file.

Used by /win, /grievance, /retro and the correction nudge. Reviewed centrally with
`mast learn` / `mast promote`.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from lib import LEARN_DIR, PROJECT_DIR, is_inside, read_jsonl, read_stdin, to_rel
from analysis.rules import compile_rule


def fail(message: str) -> None:
    print(f"record-lesson: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    try:
        candidate = json.loads(read_stdin())
    except ValueError as exc:
        fail(f"stdin must be a single JSON object ({exc})")
        return
    if not isinstance(candidate, dict) or not isinstance(candidate.get("lesson"), str) or not candidate["lesson"].strip():
        fail('"lesson" (string) is required')
        return

    candidate["lesson"] = candidate["lesson"].strip()
    candidate["kind"] = "win" if candidate.get("kind") == "win" else "grievance"
    candidate["scope"] = "project" if candidate.get("scope") == "project" else "global"
    candidate.setdefault("source", candidate["kind"])

    # Exemplars must be real files inside this repo; stored repo-relative.
    raw = candidate.pop("example", None)
    examples = candidate.get("examples") or ([] if raw is None else [raw])
    examples = examples if isinstance(examples, list) else [examples]
    resolved = [rel for rel in (to_rel(e) for e in examples) if is_inside(rel) and (PROJECT_DIR / rel).exists()]
    if len(examples) > len(resolved):
        print("record-lesson: dropped example paths that do not exist in this repo", file=sys.stderr)
    if resolved:
        candidate["examples"] = resolved
    else:
        candidate.pop("examples", None)

    rule = candidate.get("rule")
    if rule is not None:
        if not isinstance(rule, dict) or not rule.get("id") or compile_rule(rule) is None:
            print("record-lesson: dropping invalid rule (needs id + a valid Python regex pattern)", file=sys.stderr)
            candidate.pop("rule")
        elif rule.get("severity") not in ("warn", "block"):
            rule["severity"] = "warn"

    candidate["id"] = hashlib.sha1(candidate["lesson"].lower().encode(), usedforsecurity=False).hexdigest()[:8]
    candidate["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    candidate["project"] = PROJECT_DIR.name

    file = LEARN_DIR / "candidates.jsonl"
    if any(existing.get("id") == candidate["id"] for existing in read_jsonl(file)):
        print(f"Lesson {candidate['id']} already recorded.")
        return
    LEARN_DIR.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(candidate) + "\n")
    print(f"Recorded {candidate['kind']} {candidate['id']} ({candidate['scope']}): {candidate['lesson']}")


if __name__ == "__main__":
    main()
