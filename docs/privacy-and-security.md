# Privacy & security

## What mast stores

Everything stays on your machine. mast has no server, no telemetry and no network calls of its own.

| Where | Contents | Committed? |
| --- | --- | --- |
| `~/.mast/registry.json` | paths of exported repos and registered standards | no (outside any repo) |
| `~/.mast/decisions.json` | ids of promoted/rejected candidates | no |
| `~/.mast/learning-report.md` | the last `mast learn` report | no |
| `~/.mast/candidates.jsonl`, `distilled.json` | `--distill` output and bookkeeping | no |
| `<repo>/.claude/standards/learning/events.jsonl` | rule hits (rule id, file path), gate results, review findings (summary, file, line), and the **first 500 characters of messages detected as praise or corrections** | no — gitignored |
| `<repo>/.claude/standards/learning/candidates.jsonl` | recorded wins and grievances | no — gitignored |
| `<repo>/.claude/standards/.state/` | per-session gate state | no — gitignored |

`MAST_STATE_DIR` overrides `~/.mast`. Turn off message logging with `"learning": { "captureCorrections": false }`.

## What leaves your machine

Only `mast learn --distill` sends anything: up to 60 praise/correction excerpts, through **your own** `claude` CLI, under your account and its data policies. Nothing else in mast talks to the network.

The exported files (standards, hooks, analyzer, commands, config) are meant to be committed to each repo — review them like any other change.

## Hooks run code

mast's hooks are Python scripts that run with your permissions whenever Claude Code triggers them. They also run the `format`, `lint`, `qa.relatedTests` and `stopChecks` commands from `.claude/standards.config.json` through your shell. Treat that config — and any standard you register — like code: review changes, and only register standards you trust.

## Rules are guardrails, not a sandbox

`block` rules, protected paths and shell guards stop a cooperative agent from making common mistakes (writing secrets, force-pushing, editing `.env`). They are regex checks on tool input, not a security boundary: content written through arbitrary shell commands isn't checked by file rules. Keep Claude Code's own permission settings as your real boundary.

## Public core, private standards

mast is designed so the core can be public while your company's conventions stay private:

| Public (the core) | Private (your standard) |
| --- | --- |
| analyzer, hooks, loops, stock packs | company context, repo names, domains, markets |
| universal wins and grievances | internal helpers, integration and vendor contracts |
| generic rules and reviewer | security-assessment checklists, company rules and reviewer |

`mast leakcheck` enforces the split. Before pushing a public repo it scans every file git would publish for secrets, credentials, home paths, emails, and every registered standard's name and `privateTerms`, and exits `1` on any hit. It doesn't scan history — pair it with a history scanner such as gitleaks if a repo has ever held secrets.

The one path from private to public is `mast promote --pack core`; promote company lessons into your standard instead, and keep universal ones free of company names.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
