---
description: Record a mistake so it is never repeated (mast learning loop)
argument-hint: [what went wrong — optional, inferred from the session if omitted]
---

Record a **grievance** for the mast learning loop: $ARGUMENTS

1. Name the mistake precisely — what was done, and what should have been done instead. If no text was given, infer it from the correction the user just made.
2. Write it as ONE imperative rule (max 30 words) that would have prevented it, e.g. "Never query inside a loop — fetch with a single filtered query and index the result in memory." Add a one-line `why`.
3. If the mistake is detectable by a regex on a single line, include a `rule` so the hooks can catch it next time: `{"id":"<pack>/<name>","severity":"warn","globs":["**/*.py"],"pattern":"<python regex>","message":"<what to do instead>"}`. If mast's analyzer already has a check for it, reference the check id in `why` instead of inventing a rule.
4. Scope: `project` if it only applies here, otherwise `global`.
5. Record it by running exactly (fill in the JSON, keep the heredoc):

```bash
python3 .claude/standards/hooks/record_lesson.py <<'JSON'
{"kind":"grievance","source":"correction","scope":"global","lesson":"...","why":"..."}
JSON
```

6. Reply with the recorded grievance in one line.
