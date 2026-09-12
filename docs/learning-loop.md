# Learning loop

The loop turns what happens in real sessions into standards that stop it happening again.

```
session ──▶ praise / correction / violation / analyzer finding / review finding
   │             │
   │             └─▶ events.jsonl (per repo, gitignored)
   │
   ├─▶ /win, /grievance, /retro ──▶ candidates.jsonl
   │
mast learn ──▶ report + recommendations
   │
mast promote <id> ──▶ a pack's wins.md / grievances.md (+ rules.json, + exemplars)
   │
mast sync ──▶ every exported repo
```

## What gets recorded automatically

| Event | When |
| --- | --- |
| `praise` / `correction` | your message looks like either (UserPromptSubmit) |
| `violation` | a text rule fired, with severity and file |
| `analysis_finding` | an analyzer finding, with the check id |
| `lint_fail` | lint failed on an agent edit |
| `qa_run` | the test gate ran, and whether it passed |
| `review`, `review_finding`, `review_win` | the reviewer's verdict and findings |
| `gate_block`, `gate_giveup` | which end-of-turn gate blocked, and when the loop gave up |
| `turn`, `commit` | turn size, whether it was gated, commits made |

All of it stays in the repo, gitignored, and never leaves the machine.

## Wins and grievances

A **win** is an approach you liked, written as one reproducible instruction, with the files that
exemplify it. Claude is told to reuse it and to read the exemplars first.

A **grievance** is a mistake you corrected, written as one imperative rule. A repeat is automatically
a high-severity review finding.

Both start as **candidates**, recorded by `/win`, `/grievance` or `/retro` (or by `mast learn --distill`),
and become shared standards only when you promote them.

```bash
mast learn                      # the report, with pending candidates and recommendations
mast promote 3f2a1b9c           # into the project's standard (its defaultPack)
mast promote 3f2a1b9c --pack core          # into mast itself, for a universal lesson
mast promote 3f2a1b9c --scope project      # into this repo's local-wins.md / local-lessons.md
mast reject 3f2a1b9c            # dismiss it
mast sync                       # push promoted lessons into every repo
```

A promoted win copies its exemplar files into the pack's `exemplars/` directory — **review those before
committing them**, since they come from a real repo.

A candidate can carry a `rule`, which `promote` appends to the pack's `rules.json`, so the lesson is
mechanically enforced from then on. If mast's analyzer already covers it, reference the check id in the
`why` instead of inventing a rule.

## Recommendations

`mast learn` reads the aggregated events and suggests what to change, for example:

- a rule that warns repeatedly across projects — raise it to `block`, or make the standard clearer
- a rule that keeps blocking — add an explicit bad/good example so Claude stops trying
- an analyzer check that keeps firing — write the rule into the standards doc for that area
- a gate that fires on a quarter of all turns — strengthen that instruction
- reviewer findings clustering in one category — capture the cause as a grievance
- rules with no hits in 30 days across several projects — keep as guardrails, or prune as noise

## `--distill`

```bash
mast learn --distill
```

Sends recent praise and correction *excerpts* (not your code) to your own `claude` CLI and asks for
durable lessons as JSON lines, which become candidates you still have to promote. It runs from
`~/.mast` so no project's hooks or `CLAUDE.md` load into the call, and it is the only part of mast that
talks to a model. Skip it entirely if you would rather write lessons yourself.

## Habits that make the loop work

- Say so when you like something, specifically — "this is the error handling I want" beats "nice".
- Run `/retro` at the end of a session that went badly. Honest grievances are worth more than wins.
- Promote sparingly. Ten sharp standards beat fifty vague ones, and every doc loads into every session.
- Re-read `mast learn` monthly and prune what no longer fires.
