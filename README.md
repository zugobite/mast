# mast

**Python code review, bug hunting and enterprise standards — for Claude Code.**

[![ci](https://github.com/zugobite/mast/actions/workflows/ci.yml/badge.svg)](https://github.com/zugobite/mast/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-%E2%89%A53.10-brightgreen)
![dependencies](https://img.shields.io/badge/dependencies-0-blue)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

`CLAUDE.md` tells Claude how you like Python written. mast makes sure it happens:

- **A real bug hunter, not a style checker** — 81 `ast`-based checks for the defects that survive code review and fail in production: mutable defaults, late-binding closures, mutation during iteration, swallowed exceptions, blocking calls in `async def`, N+1 queries, accidental O(n²), SQL and shell injection, unverified tokens. Every finding says why it matters and how to fix it.
- **Enforcement, not suggestions** — the analyzer runs *before* a file is written and again on every edit; high-severity findings are refused. Secrets, `breakpoint()`, destructive SQL, package publishing and force-pushes are blocked outright.
- **Python standards Claude actually reads** — typing, exceptions and logging, testing, security, efficiency, plus Django, FastAPI, data and packaging conventions, imported into every repo's `CLAUDE.md` and kept in sync from one place.
- **A QA & review loop** — before Claude says "done", the analyzer runs over everything it changed, the tests covering those files run, and an independent reviewer agent checks the diff against your standards. Claude fixes what fails.
- **Commits as it goes** — small Conventional Commits on a branch, only the files Claude touched. Pushing stays your call.
- **A learning loop** — your praise becomes **wins** to reproduce; your corrections become **grievances** never to repeat. You approve what gets promoted; every repo gets it on the next sync.

Zero dependencies, standard library only. The hooks and the analyzer are committed into each repo, so your teammates need nothing but Python.

> mast is the Python counterpart to [keel](https://github.com/zugobite/keel), which does this for JavaScript and TypeScript. Same architecture, different engine: where keel leans on ESLint and Prettier, mast ships its own analyzer, because Python's real defects are semantic rather than syntactic.

## Quick start

```bash
git clone https://github.com/zugobite/mast ~/code/mast
pip install -e ~/code/mast        # or: pipx install -e ~/code/mast

mast check src                    # hunt for bugs right now, in any Python repo
mast export ~/code/my-api         # detects the stack, writes standards + hooks
mast doctor ~/code/my-api         # verifies the setup and self-tests the hooks
```

Commit the generated files, start a new Claude Code session in the repo, and work as usual. → [Getting started](docs/getting-started.md)

## The analyzer on its own

`mast check` is useful with or without Claude Code — in a pre-commit hook, in CI, or just in your terminal:

```console
$ mast check src/shop
src/shop/orders.py
  ✗ 6:20   high   bug/mutable-default
      `summarize(..., order_ids=[])` is a mutable default — the same object is shared by every call
      │ def summarize(order_ids=[], tax=0.2):
  ✗ 9:15   high   perf/n-plus-one-query
      `session.query(...)` runs once per iteration (N+1 queries) — fetch the rows in a single query before the loop
      │ row = session.query(Order).filter(Order.id == oid).first()
  ✗ 10:9   high   perf/loop-string-concat
      `out +=` inside a loop copies the whole string each iteration — collect the pieces and `''.join(...)` once
      │ out += str(row.total)
  ✗ 15:5   high   bug/blocking-call-in-async
      blocking call in async `fetch()` — time.sleep blocks the whole event loop — use `await asyncio.sleep(...)`
      │ time.sleep(1)

4 finding(s) in 1 file(s): 4 high
  bugs 2 · efficiency 2
  `mast explain <check-id>` for the reasoning and the fix.
```

```bash
mast check --diff main                 # only what this branch changed
mast check --format sarif > mast.sarif # GitHub code scanning
mast explain bug/late-binding-closure  # why it matters, and the fix
mast checks --category security        # everything it looks for
```

Exit code is 1 when anything reaches `--fail-on` (default `high`), so it drops straight into CI.
→ [All 81 checks](docs/checks.md) · [The analyzer](docs/analyzer.md)

## A session with mast

An illustrative turn:

```
you     ▸ add a bulk refund endpoint

session ▸ mast: profile fastapi · 81 checks active · recurring here: perf/n-plus-one-query (3×) · 2 proven wins
write   ▸ refunds.py — REFUSED: bug/mutable-default, sec/sql-injection (fix the content and retry)
write   ▸ refunds.py ✓ · ruff format ✓ · analyzer ✓ · ruff check ✓
edit    ▸ service.py — analyzer: perf/n-plus-one-query at line 44 → Claude batches the query
finish? ▸ analyzer over 3 changed files: clean
        ▸ tests covering them: 1 failing → Claude fixes it → green
        ▸ 78 lines changed → reviewer agent: changes requested (high: refund is not idempotent)
        ▸ Claude adds the idempotency key and a test → reviewer: pass
commit  ▸ feat(refunds): add bulk refund endpoint — only Claude's files, on feat/bulk-refund
done    ▸ summary of what changed, what was verified, and the commits made

you     ▸ perfect — that idempotency pattern is exactly what I want
        ▸ Claude records a win with refunds.py as the exemplar
later   ▸ mast learn → mast promote <id> → mast sync   (every repo now reproduces it)
```

## How it works

```
┌──────────────────────────── mast (public core) ─────────────────────────────┐
│ analyzer (81 AST checks) · CLI · hooks · QA/review loop · learning loop     │
│ stock packs: core · web · django · fastapi · data · packaging               │
└─────────────────▲──────────────────────────────────────────────────────────┘
                  │ layered on (optional)
┌─────────────────┴──────────── your standard (private) ─────────────────────┐
│ your packs · profiles · detect rules · config defaults · commands · agents  │
└─────────────────┬──────────────────────────────────────────────────────────┘
                  │ mast export / mast sync
┌─────────────────▼──────────────────── each repo ───────────────────────────┐
│ CLAUDE.md block · .claude/standards/ · hooks + analyzer in settings.json    │
│ reviewer & bug-hunter agents · /commands · your config and local lessons    │
└─────────────────┬──────────────────────────────────────────────────────────┘
                  │ praise · corrections · violations · findings · reviews
                  └──▶ mast learn ──▶ mast promote ──▶ your standard ──▶ mast sync
```

| Hook | What mast does |
| --- | --- |
| **SessionStart** | says which standards and checks are active, which wins to reuse, and what Claude keeps getting wrong in this repo |
| **UserPromptSubmit** | notices praise and corrections, logs them, and nudges Claude to record wins and grievances |
| **PreToolUse** | **analyzes the file Claude is about to write** and refuses high-severity findings; denies `block` text rules, writes to managed files and `.env`, dangerous shell commands, non-conventional commits and commits to `main` |
| **PostToolUse** | analyzes the file just edited, surfaces `warn` rules, formats and lints it, reminds Claude to checkpoint; records the reviewer's verdict |
| **Stop** | changelog and custom checks, the analyzer over everything changed this turn, the tests covering those files, the reviewer gate, then a commit checkpoint — fix rounds are capped so nothing loops forever |

The hooks run the analyzer from a copy vendored into the repo, so they work on a machine that has never installed mast.

## What lands in a repo

```
my-api/
├── CLAUDE.md                    # a managed block @importing the standards — your content is kept
└── .claude/
    ├── settings.json            # mast's hooks merged in — your settings are kept
    ├── standards.config.json    # yours: lint, tests, analyzer thresholds, gates, review
    ├── agents/                  # reviewer · bug-hunter
    ├── commands/                # /qa /hunt /ship /checkpoint /win /grievance /retro
    └── standards/
        ├── packs/               # the standards Claude reads
        ├── rules.json           # text rules the hooks enforce
        └── hooks/               # the 5 hooks + a vendored copy of the analyzer
```

Managed files are updated by `mast sync` and protected from agent edits; files marked *yours* are never overwritten. Learning data stays local and gitignored.

## Commands

| Terminal | |
| --- | --- |
| `mast check [paths…]` | the analyzer: bugs, efficiency, structure, standards, security |
| `mast explain <check-id>` | why a check exists and how to fix what it finds |
| `mast checks` | every check, with severity and category |
| `mast export [dir…]` | export into repos (auto-detects the stack; interactive picker without args) |
| `mast sync` / `mast status` | update every exported repo / see drift and feedback per repo |
| `mast doctor [dir]` | validate a repo and self-test its hooks |
| `mast learn` | wins, grievances, analyzer/review stats, and recommendations |
| `mast promote <id>` / `mast reject <id>` | turn a candidate into a standard / dismiss it |
| `mast standard init\|add\|check\|list` | create and manage private standards |
| `mast leakcheck [dir]` | make sure nothing private or secret is about to be published |

| In Claude Code | |
| --- | --- |
| `/qa` | analyzer → lint → types → tests → reviewer, until it passes |
| `/hunt` | deep bug hunt: the analyzer, then the `bug-hunter` agent with reproductions |
| `/ship` | `/qa`, changelog, commits and a PR draft — asks before pushing or opening a PR |
| `/checkpoint` | commit the work so far as Conventional Commits — only Claude's files, never pushes |
| `/win` · `/grievance` · `/retro` | record what worked, what went wrong, or both from the whole session |

Full reference: [CLI](docs/cli.md).

## Stock packs and profiles

| Profile | Packs | Picked when the repo has |
| --- | --- | --- |
| `django` | core · web · django | Django, or a `manage.py` |
| `fastapi` | core · web · fastapi | FastAPI |
| `web-service` | core · web | Flask, Starlette, aiohttp, Litestar, Sanic, Tornado… |
| `data` | core · data | pandas, numpy, polars, scikit-learn, Spark, Airflow, dbt… |
| `lib` | core · packaging | a build backend and no app framework |
| `base` | core | anything else — scripts, tooling, monorepo corners |

31 text rules across the packs (a FastAPI repo gets 19 of them), on top of the 81 analyzer checks that run everywhere. Details: [Stock packs](docs/packs.md).

## Your company layer

```bash
mast standard init ~/code/acme-python --name acme
# write packs/, add profiles and detect rules to standard.json
mast standard check ~/code/acme-python
mast export ~/code/acme-billing    # repos matching your detect rules pick your standard automatically
```

A private standard adds your own packs, profiles, detection rules, config defaults, slash commands and even a custom reviewer — while reusing everything in the core. Promoted company lessons land in the standard, not the core, and `privateTerms` + `mast leakcheck` stop company names from leaking into anything public.

→ [Building a private standard](docs/standards.md) · [full example](examples/acme-python)

## Documentation

| | |
| --- | --- |
| [Getting started](docs/getting-started.md) | install, first check, first export, first session |
| [The analyzer](docs/analyzer.md) | how it works, severities, suppression, CI, writing a check |
| [Check reference](docs/checks.md) | all 81 checks, with the reasoning and the fix |
| [Concepts](docs/concepts.md) | core, standards, packs, profiles, rules, managed vs yours |
| [CLI reference](docs/cli.md) | every command, flag and environment variable |
| [Configuration](docs/configuration.md) | every key in `standards.config.json`, with recipes |
| [Hooks](docs/hooks.md) | exactly what runs, when |
| [Writing rules](docs/rules.md) | your own `block` / `warn` text checks |
| [QA & review loop](docs/qa-and-review.md) | the analyzer gate, related tests, the reviewer agent |
| [Commit discipline](docs/commits.md) | checkpoint commits, Conventional Commits, branch protection |
| [Learning loop](docs/learning-loop.md) | wins, grievances, `learn`, `promote` |
| [Building a private standard](docs/standards.md) | your company layer on top of mast |
| [Stock packs](docs/packs.md) | what ships with mast |
| [Privacy & security](docs/privacy-and-security.md) | what's stored, where, and what never leaves your machine |
| [Troubleshooting](docs/troubleshooting.md) | common issues and fixes |

## FAQ

**Is this a linter? Do I still need ruff?**
Keep ruff. It is fast and excellent at style, imports and syntax-level rules. mast looks for different things — semantic defects, algorithmic cost, architectural drift — and explains each one well enough for an agent to act on it. The hooks run both.

**Does mast send my code anywhere?**
No. mast has no server and makes no network calls. Only `mast learn --distill` sends short feedback excerpts, through your own `claude` CLI. See [Privacy & security](docs/privacy-and-security.md).

**Do teammates need mast installed?**
Not to benefit from it: the standards, hooks, analyzer, commands and agents are committed into the repo and run on plain `python3`. mast itself is needed to export, sync, run the learning loop, and to use `mast check` from the terminal.

**Will it overwrite my `CLAUDE.md` or settings?**
No. It owns one marked block in `CLAUDE.md` and its own hook entries in `settings.json`; everything else is left alone. Per-repo config and local lessons are created once and never overwritten.

**A check is wrong about my code.**
Three ways out, in order of preference: fix the code, add `# mast: ignore[check/id]` with a reason on that line, or turn the check off for the project in `analysis.ignore`. If it is wrong in general, [open an issue](https://github.com/zugobite/mast/issues) — a false positive is a bug.

**Will Claude commit or push on its own?**
It commits its own changes on a branch as it works — small Conventional Commits, only the files it touched — so nothing is lost. It never pushes: pushing to `main`, force-pushing and `--no-verify` are refused, and anything else is your call. Tune or turn it off with `commits` in the config.

**Does it work with Cursor?**
`mast export --cursor` mirrors the standards into `.cursor/rules/*.mdc`, so both tools read the same source. The hooks and loops are Claude Code features; `mast check` works anywhere.

**Windows?**
Use WSL — configured commands run through a POSIX shell.

## Requirements

Python ≥ 3.10, git, and Claude Code with hooks support. macOS or Linux (Windows via WSL). No third-party packages.

## Contributing, security, license

- [Contributing](CONTRIBUTING.md) — setup, layout, how to add a check, PR checklist
- [Security policy](SECURITY.md) — report vulnerabilities privately
- [Changelog](CHANGELOG.md)
- [MIT](LICENSE)
