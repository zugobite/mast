# The analyzer

`mast check` is the part of mast that finds defects. It parses each file with the standard library's
`ast` module and runs 81 checks over the tree — no network, no third-party packages, no execution of
your code.

```bash
mast check                      # the current directory
mast check src tests            # specific paths
mast check --diff main          # only findings on lines this branch changed
mast check --select security    # one category
mast check --explain            # include the reasoning and the fix inline
mast check --format json        # machine-readable
mast check --format sarif       # GitHub code scanning, most CI dashboards
```

## What it looks for

| Category | Count | What it is |
| --- | --- | --- |
| `bugs` | 27 | defects that make the code do the wrong thing at runtime |
| `efficiency` | 15 | correct code that does avoidable work — accidental O(n²), a query per row |
| `structure` | 10 | size, complexity, duplication, import cycles |
| `standards` | 14 | typed public APIs, real exceptions, real logging, no hidden shared state |
| `security` | 15 | injection, secrets, deserialization, TLS, tokens, markup |

Two checks work across the whole run rather than one file at a time:
`structure/duplicate-block` (alpha-renamed function bodies that match) and `structure/import-cycle`
(modules that import each other at import time).

→ [The full reference](checks.md), or `mast explain <check-id>`.

## Severity

| Severity | Meaning | Default behaviour in Claude Code |
| --- | --- | --- |
| `high` | a defect, or a security hole — this will bite | a write is **refused**; the end of a turn is **gated** |
| `medium` | should be fixed in this change | reported to Claude with the fix |
| `low` | polish, idiom, small waste | reported to Claude |

`--fail-on high|medium|low|none` controls the exit code (default `high`). In a project, `analysis.blockOn`
and `analysis.gateOn` control what the hooks refuse and gate on.

## Settings

Thresholds and switches come from, in order: `--config <file>`, `.claude/standards.config.json`
(`analysis` block), `.mast.json`, `[tool.mast.analysis]` in `pyproject.toml`, then the defaults.
`mast check --stats` prints which one was used.

```toml
# pyproject.toml — for a repo that does not use Claude Code
[tool.mast.analysis]
maxFunctionLines = 60
maxComplexity = 12
requireTypeHints = true
requireDocstrings = false
ignore = ["perf/append-only-loop"]
```

| Key | Default | Meaning |
| --- | --- | --- |
| `maxFunctionLines` | 50 | `structure/long-function` |
| `maxComplexity` | 10 | `structure/high-complexity` (cyclomatic) |
| `maxArguments` | 6 | `structure/too-many-arguments` (`self`/`cls` not counted) |
| `maxNesting` | 4 | `structure/deep-nesting` |
| `maxClassMethods` / `maxClassLines` | 20 / 400 | `structure/large-class` |
| `maxModuleLines` | 600 | `structure/large-module` |
| `maxLocals` | 15 | `structure/too-many-locals` |
| `minDuplicateStatements` | 6 | how much body `structure/duplicate-block` needs before it reports |
| `requireTypeHints` | `true` | `api/missing-type-hints` |
| `requireDocstrings` | `false` | `api/missing-docstring` |
| `select` / `ignore` | `[]` | check ids, categories (`bugs`) or prefixes (`sec/`) |
| `testGlobs` | `tests/**`, `**/test_*.py`, … | files treated as tests (assertions fine, typing not required) |
| `scriptGlobs` | `scripts/**`, `**/cli.py`, `**/__main__.py`, … | files treated as entry points (`print` and `sys.exit` fine) |
| `excludeGlobs` | migrations, `.venv`, `build`, generated protobufs… | never analyzed |

## Turning a check off

In order of preference:

1. **Fix the code.** Most findings are a real defect or a real cost.
2. **Suppress one line**, with a reason:
   ```python
   result = subprocess.run(command, shell=True)  # mast: ignore[sec/shell-injection] operator-supplied command
   ```
   A bare `# mast: ignore` silences every check on that line. The comment may also sit on the line
   directly above. Listing the id is strongly preferred — it keeps the next defect visible.
3. **Switch it off for the project** — `analysis.ignore: ["api/missing-docstring"]`, or raise a
   threshold. Record why in the repo's `CLAUDE.md` so nobody re-litigates it.

If a check is wrong in general, that is a bug in mast — please report it with the snippet.

## In CI

```yaml
- run: pip install -e ./mast
- run: mast check src --fail-on high
# or, to publish annotations:
- run: mast check src --format sarif > mast.sarif
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: mast.sarif
```

On an existing codebase, start with `mast check --diff origin/main --fail-on high`: new code is held
to the standard without a cleanup project first.

## In the hooks

| When | What runs |
| --- | --- |
| Claude is about to `Write` a `.py` file | the whole new content is analyzed; `analysis.blockOn` findings refuse the write |
| after any edit | the file on disk is analyzed (after formatting); high findings come back for a fix, the rest as a note |
| the turn is ending | every Python file changed this turn is analyzed; `analysis.gateOn` findings block the turn |

The hooks import a copy of the analyzer vendored into `.claude/standards/hooks/analysis/` at export
time, so they work with nothing installed but Python. `mast sync` updates it.

## Writing a check

Checks live in `src/mast/analysis/{bugs,efficiency,structure,standards,security}.py`:

```python
@check(
    id="bug/mutable-default",
    category="bugs",
    severity="high",
    title="Mutable default argument",
    why="Default arguments are evaluated once, when the function is defined. …",
    fix="Default to `None` and build the container inside the body: …",
)
def mutable_default(ctx: ModuleContext) -> Iterator[Finding]:
    for func in ctx.functions():
        ...
        yield ctx.report(default, f"`{func.name}()` has a mutable default argument", symbol=func.name)
```

`ctx` (see `analysis/context.py`) gives you the parsed tree plus the lookups a check needs:
`ctx.nodes(ast.Call)`, `ctx.parent`, `ctx.enclosing_function`, `ctx.enclosing_loop`,
`ctx.qualname` (resolves `np.array` → `numpy.array`), `ctx.is_test`, `ctx.settings`.

House rules:

- **A finding must be a defect or a real cost.** If a careful reviewer would say "that's fine", it is a false positive.
- `why` explains the consequence, not the rule. `fix` is concrete enough to apply.
- Precision over recall: prefer missing an unusual case to crying wolf. `ctx.is_test`, `ctx.is_script`
  and a comment check are all legitimate ways to stay quiet.
- Every check needs tests both ways — the defect is caught, the correct version is silent
  (`tests/test_bugs.py` and friends).
- Project-wide checks take the list of module contexts and set `project_wide=True`.
- Keep the package dependency-free and self-contained: it is vendored into every exported repo.
- Run `python scripts/generate_docs.py` afterwards so `docs/checks.md` matches the registry.
