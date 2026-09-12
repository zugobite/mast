---
description: Retro on this session — record the wins and grievances worth keeping
---

Run a short retrospective on this session.

1. Re-read the session: what the user asked for, what you did, where they corrected you, and what they praised.
2. List the **durable** lessons only — things that should change how future sessions work, not task-specific instructions. Three or four at most; be honest about mistakes, including ones the user did not notice.
3. For each, decide `win` or `grievance`, and `global` or `project` scope.
4. Record each one with `python3 .claude/standards/hooks/record_lesson.py` (see `/win` and `/grievance` for the exact JSON shape). Wins that depend on code include the exemplar files.
5. Show the user a table of what you recorded (kind · scope · lesson) and tell them they review the candidates with `mast learn` and promote them with `mast promote <id>`.
