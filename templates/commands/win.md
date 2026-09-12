---
description: Save something Claude did well so it gets reproduced (mast learning loop)
argument-hint: [what worked — optional, inferred from the session if omitted]
---

Record a **win** for the mast learning loop: $ARGUMENTS

1. Identify exactly what the user liked. If no text was given, infer it from the most recent work the user approved. Be specific — the structure, the API shape, the test pattern, the error handling — never just "good code".
2. Write it as ONE reproducible instruction (max 30 words) starting with a verb, e.g. "Validate request bodies in a pydantic model with extra=forbid, and keep the ORM type out of the response." Add a one-line `why` (what the user valued).
3. Put 1–3 repo-relative files that best exemplify it in `examples`. Promoted global wins copy these into the shared standards as reference exemplars.
4. Scope: `project` if it depends on this repo's code or conventions, otherwise `global`.
5. Record it by running exactly (fill in the JSON, keep the heredoc):

```bash
python3 .claude/standards/hooks/record_lesson.py <<'JSON'
{"kind":"win","source":"win","scope":"global","lesson":"...","why":"...","examples":["src/app/..."]}
JSON
```

6. Reply with the recorded win in one line.
