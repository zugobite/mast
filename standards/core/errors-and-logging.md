---
description: Exceptions and logging — failing usefully, and leaving a trail worth reading
globs: ["**/*.py"]
alwaysApply: true
---

# Errors and logging

## Exceptions

- Raise specific exceptions. `raise Exception("failed")` gives callers nothing to branch on; a domain exception (`class PaymentDeclined(AppError)`) lets them retry, report or surface it correctly.
- Define one base exception per package (`class AppError(Exception)`) and derive from it, so callers can catch your errors without catching everything.
- Catch only what you can handle, and only around the statement that raises it. A `try` block wrapped around fifty lines hides which one failed.
- Never swallow an exception. `except ...: pass` discards the failure and the traceback — log it, re-raise it, or return an explicit fallback, and say in a comment when ignoring is deliberate.
- Bare `except:` also catches `KeyboardInterrupt` and `SystemExit`. Use `except Exception:` when you really mean "any application error", and only at a boundary that reports it.
- Preserve the cause: `raise ValidationError(...) from exc`. Without `from`, the traceback points at your wrapper instead of the line that broke.
- Order handlers narrowest first — a broad handler above a specific one makes the specific one dead code.
- Never use `assert` for validation: `python -O` strips it. Raise explicitly.
- Exception messages state what failed and with which value, without leaking secrets or PII.

## Logging

- One logger per module: `logger = logging.getLogger(__name__)`. Never call `logging.info(...)` directly — the root logger loses the module name and cannot be filtered per component.
- Configure logging once, in the application entry point. A library configures nothing and adds no handlers.
- Never `print()` in application code. `print` has no level, no context and no destination control.
- Pass values as arguments, not f-strings: `logger.info("user %s saved", user_id)`. It skips formatting when the level is off and keeps the message groupable in aggregation.
- Inside `except`, use `logger.exception(...)` so the traceback is recorded. `logger.error(str(exc))` throws away the only part you need.
- Levels mean something: `debug` for developers, `info` for business events, `warning` for recoverable anomalies, `error` for failed operations, `critical` for lost capability.
- Never log credentials, tokens, full request bodies, or personal data. Log ids and let the operator join the data themselves.
- Attach request/correlation ids through a `contextvar` or a logging filter, so one request's lines can be found together.
