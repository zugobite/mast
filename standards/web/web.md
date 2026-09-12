---
description: HTTP service standards — layering, validation, errors, auth, pagination, async
globs: ["**/*.py"]
alwaysApply: true
---

# HTTP services

## Layering

- Three layers, one direction: **transport** (routing, serialisation, status codes) → **service** (business rules, transactions) → **repository** (data access). Business rules never import the web framework; data access never contains business rules.
- A route handler is thin: parse, call one service function, map the result to a response. If a handler has branching business logic, that logic belongs in the service layer where it can be tested without HTTP.
- Inject dependencies (session, client, clock) rather than importing singletons, so every layer is testable in isolation.

## Requests

- Validate and coerce every request body, query and path parameter through a schema at the boundary. Never read untyped dicts into business logic.
- Reject unknown fields rather than ignoring them; silent acceptance hides client bugs and is a vector for mass-assignment.
- Size-limit bodies and uploads, and set a timeout on every outbound call.
- Idempotency: any non-GET endpoint a client may retry takes an idempotency key, or is naturally idempotent. Assume every request will be delivered twice.

## Responses

- Correct status codes: 400 for a malformed request, 401 unauthenticated, 403 unauthorised, 404 missing, 409 conflict, 422 semantically invalid, 429 rate-limited, 5xx only for your own faults.
- One error shape across the API: a stable machine-readable `code`, a human `message`, and field-level detail for validation errors. Never return a raw traceback or a database error string.
- Never leak internals — table names, file paths, library versions, stack traces — in a response body.
- List endpoints are always paginated with a documented limit, a stable ordering, and a cursor or offset contract. No unbounded list.

## Auth and tenancy

- Authenticate and authorise in one place at the boundary, against the authenticated principal from the token or session — never a role, user id or tenant id taken from the request body.
- Every query is scoped by tenant/owner in the repository layer, not by the caller remembering to filter.
- Rate-limit authentication, password reset, email and anything metered.

## Operability

- Structured logs with a correlation id per request, and no PII or secrets in them.
- A health endpoint that checks the dependencies it claims to, and readiness separate from liveness.
- Database work happens in a transaction that is opened and committed in the service layer, is short, and never wraps a call to another service.
- Long or unreliable work goes to a queue or task runner, not into the request cycle.
