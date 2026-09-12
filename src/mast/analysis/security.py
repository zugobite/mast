"""Security: the Python-specific footguns that turn into incidents."""

from __future__ import annotations

import ast
import re
from typing import Iterator

from .context import PLACEHOLDER_RE, SECRET_NAME_RE, ModuleContext
from .model import Finding, check

SQL_VERB_RE = re.compile(r"^\s*(select|insert\s+into|update|delete\s+from|drop|alter|create|truncate|grant|with)\b", re.IGNORECASE)
SQL_CLAUSE_RE = re.compile(r"\b(from|where|set|values|join|into|table|returning)\b", re.IGNORECASE)
EXECUTORS = {"execute", "executemany", "executescript", "raw", "extra", "text", "from_statement", "exec_driver_sql"}


def _is_dynamic_string(ctx: ModuleContext, node: ast.AST | None) -> str | None:
    """Describe how a string was built, when it was built from non-literal parts."""
    if isinstance(node, ast.JoinedStr):
        if any(isinstance(v, ast.FormattedValue) for v in node.values):
            return "f-string"
        return None
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Mod):
            return "%-formatting"
        if isinstance(node.op, ast.Add) and not (isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant)):
            return "string concatenation"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"format", "format_map"}:
        return ".format()"
    return None


def _looks_like_sql(text: str) -> bool:
    """A SQL verb at the start plus a clause keyword — prose mentioning "from" does not qualify."""
    return bool(SQL_VERB_RE.match(text) and SQL_CLAUSE_RE.search(text))


@check(
    id="sec/sql-injection",
    category="security",
    severity="high",
    title="SQL built by string interpolation",
    why=(
        "Any value spliced into SQL text can end the string literal and append its own statement. This is the "
        "oldest remote-takeover bug there is, and an f-string makes it look tidy."
    ),
    fix="Pass parameters to the driver: `cursor.execute(\"... WHERE id = %s\", (user_id,))`, or use the ORM's query API. Never format the value in.",
)
def sql_injection(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = (ctx.dotted(call.func) or "").rsplit(".", 1)[-1]
        if name not in EXECUTORS or not call.args:
            continue
        how = _is_dynamic_string(ctx, call.args[0])
        if how:
            yield ctx.report(call.args[0], f"SQL assembled with {how} and passed to `{name}()` — use bound parameters instead")
            continue
        if isinstance(call.args[0], ast.Name):
            for assign in ctx.nodes(ast.Assign):
                if not any(isinstance(t, ast.Name) and t.id == call.args[0].id for t in assign.targets):
                    continue
                how = _is_dynamic_string(ctx, assign.value)
                if how:
                    yield ctx.report(assign, f"`{call.args[0].id}` is SQL built with {how} and executed on line {call.lineno} — use bound parameters")
    # Query strings built from interpolation, even when executed elsewhere.
    for node in ctx.nodes(ast.JoinedStr):
        literal = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
        if literal and _looks_like_sql(literal) and any(isinstance(v, ast.FormattedValue) for v in node.values):
            parent = ctx.parent(node)
            if isinstance(parent, ast.Call) and (ctx.dotted(parent.func) or "").rsplit(".", 1)[-1] in EXECUTORS:
                continue  # already reported above
            yield ctx.report(node, "SQL statement built with an f-string — interpolated values are not escaped, use bound parameters")


@check(
    id="sec/shell-injection",
    category="security",
    severity="high",
    title="Shell command built from a dynamic string",
    why=(
        "With `shell=True` the string is parsed by the shell, so `;`, `|`, backticks and `$(...)` in any "
        "interpolated value execute as commands with the process's privileges."
    ),
    fix="Pass an argument list and drop `shell=True`: `subprocess.run([\"git\", \"clone\", url], check=True)`. If the shell is unavoidable, quote with `shlex.quote`.",
)
def shell_injection(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        if not name.startswith("subprocess.") and name.rsplit(".", 1)[-1] not in {"run", "call", "check_call", "check_output", "Popen"}:
            continue
        shell = ctx.keyword(call, "shell")
        if shell is None or not (isinstance(shell.value, ast.Constant) and shell.value.value is True):
            continue
        command = call.args[0] if call.args else None
        how = _is_dynamic_string(ctx, command)
        if how or isinstance(command, ast.Name):
            detail = how or "a variable"
            yield ctx.report(call, f"`shell=True` with a command built from {detail} — pass an argument list instead")
        else:
            yield ctx.report(call, "`shell=True` runs the command through a shell — pass an argument list instead")


@check(
    id="sec/os-system",
    category="security",
    severity="high",
    title="`os.system` / `os.popen` used",
    why="These always run a shell, give no way to pass arguments safely, and discard the exit status detail — every input becomes shell syntax.",
    fix="Use `subprocess.run([...], check=True, capture_output=True)` with an argument list.",
)
def os_system(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        if name in {"os.system", "os.popen", "commands.getoutput", "os.spawnl", "os.execl"}:
            yield ctx.report(call, f"`{name}()` runs a shell — use `subprocess.run([...])` with an argument list")


@check(
    id="sec/eval-exec",
    category="security",
    severity="high",
    title="`eval` / `exec` on a non-literal",
    why="Evaluating a string executes arbitrary Python with full process privileges. Any path from user input, a database row or a config file to this call is remote code execution.",
    fix="Parse the data instead: `json.loads`, `ast.literal_eval`, or an explicit dispatch dict mapping names to functions.",
)
def eval_exec(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        if name not in {"eval", "exec", "compile", "__import__"}:
            continue
        argument = call.args[0] if call.args else None
        if isinstance(argument, ast.Constant):
            continue
        yield ctx.report(call, f"`{name}()` on a computed value executes arbitrary code — parse the input instead")


@check(
    id="sec/unsafe-deserialization",
    category="security",
    severity="high",
    title="Untrusted data deserialized into objects",
    why=(
        "`pickle`, `yaml.load` and `marshal` reconstruct arbitrary objects, which means they can be made to "
        "call arbitrary code while loading. Caches, queues and uploaded files are all attacker-reachable."
    ),
    fix="Use `json` for data, `yaml.safe_load` for YAML, and a schema (pydantic, dataclass + validation) to rebuild objects.",
)
def unsafe_deserialization(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        tail = name.rsplit(".", 1)[-1]
        if name in {"pickle.load", "pickle.loads", "cPickle.load", "cPickle.loads", "dill.load", "dill.loads", "marshal.load", "marshal.loads", "shelve.open"}:
            yield ctx.report(call, f"`{name}()` can execute code while loading — use a data format such as JSON")
        elif tail in {"load", "load_all"} and name.startswith(("yaml.", "ruamel")):
            loader = ctx.keyword(call, "Loader")
            safe = loader is not None and "safe" in (ctx.dotted(loader.value) or "").lower()
            if not safe:
                yield ctx.report(call, "`yaml.load()` without a safe loader can instantiate arbitrary objects — use `yaml.safe_load()`")


@check(
    id="sec/hardcoded-secret",
    category="security",
    severity="high",
    title="Credential hardcoded in source",
    why=(
        "A secret in source is a secret in git history, in every clone and in every CI log. Rotating it means "
        "rewriting history, and the leak usually outlives the person who added it."
    ),
    fix="Read it from the environment or a secret manager: `os.environ[\"DB_PASSWORD\"]`. Keep a placeholder in `.env.example` instead.",
)
def hardcoded_secret(ctx: ModuleContext) -> Iterator[Finding]:
    if ctx.is_test:
        return
    for stmt in ctx.nodes(ast.Assign, ast.AnnAssign, ast.keyword):
        if isinstance(stmt, ast.keyword):
            names, value = [stmt.arg or ""], stmt.value
        else:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            names = [ctx.dotted(t) or "" for t in targets]
            value = stmt.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        text = value.value
        if len(text) < 8 or PLACEHOLDER_RE.match(text) or text.startswith(("${", "{{", "<")):
            continue
        for name in names:
            if SECRET_NAME_RE.search(name.rsplit(".", 1)[-1]):
                yield ctx.report(stmt, f"`{name}` is assigned a literal secret — load it from the environment or a secret manager", symbol=name)
                break


@check(
    id="sec/tls-verification-disabled",
    category="security",
    severity="high",
    title="TLS certificate verification disabled",
    why=(
        "With verification off, any machine on the path can present its own certificate and read or rewrite "
        "the traffic. It usually starts as a workaround for a staging certificate and ships to production."
    ),
    fix="Keep `verify=True` and install the correct CA bundle (`verify=\"/path/to/ca.pem\"` for an internal CA).",
)
def tls_verification_disabled(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        if name in {"ssl._create_unverified_context"}:
            yield ctx.report(call, "unverified SSL context — certificates are not checked")
            continue
        verify = ctx.keyword(call, "verify")
        if verify is not None and isinstance(verify.value, ast.Constant) and verify.value.value is False:
            yield ctx.report(call, f"`verify=False` on `{name or 'request'}()` disables certificate checking — traffic can be intercepted")
        check_hostname = ctx.keyword(call, "check_hostname")
        if check_hostname is not None and isinstance(check_hostname.value, ast.Constant) and check_hostname.value.value is False:
            yield ctx.report(call, "`check_hostname=False` accepts a certificate for any host")


@check(
    id="sec/jwt-unverified",
    category="security",
    severity="high",
    title="Token signature not verified",
    why="Decoding a JWT without verifying the signature means anyone can mint a token with any claims — it is authentication that authenticates nothing.",
    fix="Verify with the expected key and algorithms: `jwt.decode(token, key, algorithms=[\"RS256\"], audience=...)`.",
)
def jwt_unverified(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        if name.rsplit(".", 1)[-1] != "decode":
            continue
        verify = ctx.keyword(call, "verify")
        if verify is not None and isinstance(verify.value, ast.Constant) and verify.value.value is False:
            yield ctx.report(call, "`verify=False` on a token decode — the signature is not checked")
        options = ctx.keyword(call, "options")
        if options is not None and isinstance(options.value, ast.Dict):
            for key, value in zip(options.value.keys, options.value.values):
                is_verify_key = isinstance(key, ast.Constant) and str(key.value).startswith("verify")
                if is_verify_key and isinstance(value, ast.Constant) and value.value is False:
                    yield ctx.report(call, f"`{key.value}: False` on a token decode — the signature is not checked")


@check(
    id="sec/weak-hash",
    category="security",
    severity="medium",
    title="Broken hash used for security",
    why="MD5 and SHA-1 are collision-broken: two different inputs can be made to hash the same, so they cannot back a signature, token or integrity check.",
    fix="Use SHA-256+ for integrity, and `bcrypt`/`argon2`/`scrypt` for passwords. For non-security uses pass `usedforsecurity=False`.",
)
def weak_hash(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        algorithm = name.rsplit(".", 1)[-1]
        if not name.startswith("hashlib.") or algorithm not in {"md5", "sha1"}:
            continue
        if ctx.keyword(call, "usedforsecurity") is not None:
            continue
        yield ctx.report(call, f"`hashlib.{algorithm}()` is collision-broken — use SHA-256+, or a password hash for credentials")


@check(
    id="sec/random-for-secrets",
    category="security",
    severity="medium",
    title="`random` used to generate a secret",
    why="`random` is a predictable Mersenne Twister seeded from the clock. An attacker who sees a few outputs can reconstruct the state and derive every token you issue.",
    fix="Use `secrets.token_urlsafe()`, `secrets.token_hex()` or `secrets.choice()`.",
)
def random_for_secrets(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        if not name.startswith("random."):
            continue
        holder = ctx.parent(call)
        target_names: list[str] = []
        if isinstance(holder, ast.Assign):
            target_names = [ctx.dotted(t) or "" for t in holder.targets]
        elif isinstance(holder, (ast.AnnAssign, ast.keyword)):
            target_names = [ctx.dotted(getattr(holder, "target", None)) or getattr(holder, "arg", "") or ""]
        elif isinstance(holder, ast.Return):
            func = ctx.enclosing_function(call)
            target_names = [func.name if func else ""]
        suspicious = any(
            SECRET_NAME_RE.search(name.rsplit(".", 1)[-1]) or "nonce" in name.lower() or "salt" in name.lower()
            for name in target_names
            if name
        )
        if suspicious:
            yield ctx.report(call, f"`{name}()` is predictable — use the `secrets` module for anything security-sensitive")


@check(
    id="sec/insecure-temp-file",
    category="security",
    severity="medium",
    title="Predictable temporary file",
    why="`mktemp` only returns a name, so another process can create or swap that path before you open it (a TOCTOU race) and have your code write to, or read, a file it controls.",
    fix="Use `tempfile.NamedTemporaryFile()` or `tempfile.mkstemp()`, which create the file atomically.",
)
def insecure_temp_file(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        if name == "tempfile.mktemp":
            yield ctx.report(call, "`tempfile.mktemp()` is racy — use `NamedTemporaryFile` or `mkstemp`")
        elif name in {"os.chmod"} and len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
            mode = call.args[1].value
            if isinstance(mode, int) and mode & 0o002:
                yield ctx.report(call, f"`chmod` grants world-write ({oct(mode)}) — any local user can modify the file")


@check(
    id="sec/debug-enabled",
    category="security",
    severity="high",
    title="Debug mode enabled in source",
    why=(
        "Debug mode serves tracebacks with source, local variables and settings to anyone who triggers an "
        "error, and in Flask/Werkzeug it can expose an interactive console. Committed as `True`, it ships."
    ),
    fix="Read it from the environment and default to off: `DEBUG = os.environ.get(\"DEBUG\") == \"1\"`.",
)
def debug_enabled(ctx: ModuleContext) -> Iterator[Finding]:
    for stmt in ctx.nodes(ast.Assign, ast.AnnAssign):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        names = {(ctx.dotted(t) or "").rsplit(".", 1)[-1] for t in targets}
        if "DEBUG" in names and isinstance(stmt.value, ast.Constant) and stmt.value.value is True:
            yield ctx.report(stmt, "`DEBUG = True` in source — drive it from the environment, defaulting to off")
    for call in ctx.nodes(ast.Call):
        debug = ctx.keyword(call, "debug")
        if debug is not None and isinstance(debug.value, ast.Constant) and debug.value.value is True:
            yield ctx.report(call, "`debug=True` exposes tracebacks and an interactive console — drive it from the environment")


@check(
    id="sec/permissive-cors",
    category="security",
    severity="medium",
    title="CORS allows any origin",
    why=(
        "`*` lets any site call the API from a visitor's browser. Combined with cookie credentials it is a "
        "cross-site request forgery hole that bypasses same-origin protection entirely."
    ),
    fix="List the origins you serve, and never pair a wildcard with `allow_credentials=True`.",
)
def permissive_cors(ctx: ModuleContext) -> Iterator[Finding]:
    for node in ctx.nodes(ast.keyword, ast.Assign):
        if isinstance(node, ast.keyword):
            name, value = node.arg or "", node.value
        else:
            name = ",".join((ctx.dotted(t) or "") for t in node.targets)
            value = node.value
        if not any(token in name.upper() for token in ("ALLOW_ORIGIN", "ALLOWED_HOST", "CORS_ORIGIN", "ORIGINS")):
            continue
        values = value.elts if isinstance(value, (ast.List, ast.Tuple, ast.Set)) else [value]
        if any(isinstance(v, ast.Constant) and v.value in {"*", "*.*"} for v in values):
            yield ctx.report(node, f"`{name}` allows any origin — list the origins you actually serve")


@check(
    id="sec/unsafe-markup",
    category="security",
    severity="high",
    title="HTML escaping bypassed",
    why="Marking a computed string as safe turns any value inside it into HTML, so user content becomes script — stored XSS that runs with the victim's session.",
    fix="Let the template escape it. If markup must be composed, build it with `format_html(...)` or sanitise with a allowlist (`bleach`, `nh3`).",
)
def unsafe_markup(ctx: ModuleContext) -> Iterator[Finding]:
    for call in ctx.nodes(ast.Call):
        name = (ctx.qualname(call.func) or "").rsplit(".", 1)[-1]
        if name not in {"mark_safe", "Markup"} or not call.args:
            continue
        if isinstance(call.args[0], ast.Constant):
            continue
        yield ctx.report(call, f"`{name}()` on a computed string disables HTML escaping — XSS risk")
    for keyword in ctx.nodes(ast.keyword):
        if keyword.arg == "autoescape" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False:
            yield ctx.report(keyword, "`autoescape=False` disables HTML escaping for the whole template environment")


@check(
    id="sec/unsafe-xml",
    category="security",
    severity="medium",
    title="XML parsed with the standard library",
    why=(
        "The stdlib XML parsers resolve external entities and expand nested ones, so untrusted XML can read "
        "local files (XXE) or exhaust memory (billion laughs) before your code sees any data."
    ),
    fix="Parse untrusted XML with `defusedxml` (`from defusedxml.ElementTree import parse`).",
)
def unsafe_xml(ctx: ModuleContext) -> Iterator[Finding]:
    if not ctx.imports_module("xml"):
        return
    for call in ctx.nodes(ast.Call):
        name = ctx.qualname(call.func) or ""
        if name.startswith(("xml.etree", "xml.dom", "xml.sax", "xml.parsers")) and name.rsplit(".", 1)[-1] in {"parse", "fromstring", "parseString", "XMLParser"}:
            yield ctx.report(call, f"`{name}()` resolves external entities — use `defusedxml` for untrusted input")
