---
description: Python security standards — injection, secrets, deserialization, dependencies
globs: ["**/*.py"]
alwaysApply: true
---

# Security

## Never in source

- **No secrets in code.** Keys, passwords and tokens come from the environment or a secret manager. A secret in a commit is a secret in every clone and CI log forever.
- No `DEBUG = True`, no `debug=True`, no verbose error pages outside local development. They serve source, locals and settings to whoever triggers an error.
- No `verify=False`, no unverified SSL contexts, no `check_hostname=False`. Install the right CA bundle instead.

## Injection

- SQL takes bound parameters, always: `cursor.execute("... WHERE id = %s", (user_id,))`. An f-string in a query is a takeover waiting for one apostrophe. Use the ORM's query API; when raw SQL is necessary, parameters are still not optional.
- Subprocesses take an argument list and no shell: `subprocess.run(["git", "clone", url], check=True)`. `shell=True`, `os.system` and `os.popen` turn every interpolated value into shell syntax.
- Never `eval`, `exec` or `compile` a value that did not come from your own source. Parse with `json.loads` or `ast.literal_eval`, or dispatch through an explicit dict.
- Template output is escaped by default. Do not `mark_safe`/`Markup` a computed string, and never turn autoescaping off.

## Data handling

- `pickle`, `marshal`, `dill` and `yaml.load` execute code while loading. Use JSON for data and `yaml.safe_load` for YAML; rebuild objects through a schema.
- Parse untrusted XML with `defusedxml` — the stdlib parsers resolve external entities and expand nested ones.
- `secrets`, never `random`, for tokens, passwords, nonces and salts.
- SHA-256 or better for integrity; `bcrypt`, `argon2` or `scrypt` for passwords. MD5 and SHA-1 are collision-broken.
- Validate and normalise every path built from input before touching the filesystem (`Path(base, name).resolve()` must stay inside `base`).
- `tempfile.NamedTemporaryFile`/`mkstemp`, never `mktemp`.

## Boundaries

- Authorise every request at the boundary, on the server, against the authenticated principal — never on a client-supplied role, tenant or id. Scope every query by tenant.
- Verify tokens properly: signature, algorithm allowlist, audience, expiry. `verify=False` is authentication that authenticates nothing.
- Rate-limit anything that sends mail, costs money or can be brute-forced.
- CORS lists real origins; a wildcard with credentials is a CSRF hole.
- Keep dependencies patched and pinned with a lockfile; run `pip-audit` (or equivalent) in CI. Treat a new direct dependency as a decision, not a detail.
