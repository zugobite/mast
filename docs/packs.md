# Stock packs

mast ships six organisation-agnostic packs. Profiles combine them; your own standard can reuse, extend or replace them.

## `core` — every repo

| Doc | Covers |
| --- | --- |
| `ai-workflow.md` | how to work: read before editing, smallest diff, verify before finishing, report honestly, use `/qa` |
| `coding.md` | structure, naming, idioms, the defects the analyzer exists to catch |
| `typing.md` | annotations on public APIs, `TypedDict` / dataclasses, no blanket ignores |
| `errors-and-logging.md` | typed exceptions, no silent `except`, structured logging |
| `testing.md` | a fix needs a failing test, fixtures over mocks, placement |
| `security.md` | secrets, injection, deserialization, tokens, TLS |
| `performance.md` | accidental O(n²), N+1 queries, blocking calls in `async def` |
| `git-and-changelog.md` | commit early and often, Conventional Commits, Keep a Changelog — enforced by the [commit discipline](commits.md) guards |
| `wins.md` / `grievances.md` | universal lessons promoted with `--pack core` |

| Rule | Severity | Catches |
| --- | --- | --- |
| `core/no-private-keys` | block | PEM / OpenSSH / PGP private key blocks |
| `core/no-cloud-keys` | block | AWS, Google, Anthropic/OpenAI-style, GitHub and Slack tokens |
| `core/no-debugger` | block | `breakpoint()`, `pdb` / `ipdb` / `pudb`, `debugpy.breakpoint()` |
| `core/no-hardcoded-secret` | warn | `password` / `secret` / `api_key` / `access_token` assigned a literal |
| `core/no-blanket-type-ignore` | warn | `# type: ignore` without an error code |
| `core/no-blanket-noqa` | warn | `# noqa` without a rule id |
| `core/no-todo-stubs` | warn | "TODO: implement later" stubs |
| `core/no-skip-without-reason` | warn | `@pytest.mark.skip` / `xfail` with no reason |
| `core/no-sys-path-hack` | warn | `sys.path.insert` / `append` |
| `core/no-naive-now` | warn | `datetime.now()` / `date.today()` without a timezone |
| `core/pin-requirements` | warn | unpinned lines in `requirements*.txt` |
| `core/no-docker-latest` | warn | `FROM image:latest` or an untagged `FROM` |

## `web` — HTTP services

| Doc | Covers |
| --- | --- |
| `web.md` | transport → service → repository, validation, errors, auth, pagination, async |

| Rule | Severity | Catches |
| --- | --- | --- |
| `web/no-wildcard-cors` | warn | `allow_origins=["*"]`, `CORS_ALLOW_ALL_ORIGINS = True` |
| `web/no-unbounded-query` | warn | `.objects.all()` / `session.query(...).all()` with no filter |
| `web/no-request-in-service` | warn | Flask / FastAPI / Django request objects imported in `services/` or `domain/` |

## `django`

| Doc | Covers |
| --- | --- |
| `django.md` | models and invariants, queries, thin views, settings from the environment, migrations |

| Rule | Severity | Catches |
| --- | --- | --- |
| `django/no-debug-true` | block | `DEBUG = True` in settings |
| `django/no-literal-secret-key` | block | a committed `SECRET_KEY = "…"` |
| `django/no-allowed-hosts-wildcard` | warn | `ALLOWED_HOSTS = ["*"]` |
| `django/no-raw-sql-format` | block | `.raw()` / `.extra()` built with an f-string, `%` or `.format()` |
| `django/no-objects-get-unguarded` | warn | `.objects.get()` in a view or API module |

## `fastapi`

| Doc | Covers |
| --- | --- |
| `fastapi.md` | request/response schemas, dependencies, async correctness, background work |

| Rule | Severity | Catches |
| --- | --- | --- |
| `fastapi/no-sync-client-in-async` | warn | `import requests` (blocks the event loop) |
| `fastapi/no-orm-response` | warn | `response_model=` pointing at an ORM / table / entity class |
| `fastapi/no-mutable-field-default` | warn | `list` / `dict` / `set` field defaulted to `[]` or `{}` |
| `fastapi/no-env-in-handler` | warn | `os.environ` / `os.getenv` in a router or route module |

These encode one opinionated FastAPI style. If yours differs, adjust per repo with `disabledRules` / `severityOverrides`, or replace the pack in your standard.

## `data` — pipelines and notebooks

| Doc | Covers |
| --- | --- |
| `data.md` | declared schemas, money and ids, joins, reproducibility, notebooks vs production |

| Rule | Severity | Catches |
| --- | --- | --- |
| `data/no-chained-assignment` | warn | `]['col'] =` and `.ix[` |
| `data/no-inplace` | warn | `inplace=True` |
| `data/no-read-csv-untyped` | warn | `read_csv(...)` without `dtype=` / `usecols=` / `converters=` |
| `data/no-notebook-secret` | block | credentials assigned in a `.ipynb` |

## `packaging` — libraries and CLIs

| Doc | Covers |
| --- | --- |
| `packaging.md` | `src/` layout, public API, versioning, dependency ranges, no import-time side effects |

| Rule | Severity | Catches |
| --- | --- | --- |
| `packaging/no-print-in-library` | warn | `print(` under `src/` (not `__main__.py` / a CLI module) |
| `packaging/no-exact-pin-in-library` | warn | `package==1.2.3` in a library's `pyproject.toml` |
| `packaging/no-setup-py-install` | warn | `python setup.py install` / `develop` in docs or scripts |

## Stock profiles

| Profile | Packs | Picked when the repo has |
| --- | --- | --- |
| `django` | core · web · django | Django, or a `manage.py` |
| `fastapi` | core · web · fastapi | FastAPI |
| `web-service` | core · web | Flask, Starlette, aiohttp, Litestar, Sanic, Tornado… |
| `data` | core · data | pandas, numpy, polars, scikit-learn, Spark, Airflow, dbt, or notebooks |
| `lib` | core · packaging | a build backend and no app framework |
| `base` | core | anything else — scripts, tooling, monorepo corners |

A FastAPI repo therefore gets 19 of the 31 text rules (core 12 + web 3 + fastapi 4), on top of the 81 analyzer checks that run everywhere.

## Contributing a pack

Stock packs must stay organisation-agnostic: no company names, internal helpers or private contracts. See [CONTRIBUTING.md](../CONTRIBUTING.md).
