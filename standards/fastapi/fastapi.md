---
description: FastAPI standards — schemas, dependencies, async correctness, background work
globs: ["**/*.py"]
alwaysApply: true
---

# FastAPI

## Schemas

- Separate pydantic models per direction: a request model, a response model, and the internal/ORM type. Never expose an ORM object directly — it leaks columns the API never promised.
- Declare `response_model` (or an annotated return type) on every route, so the contract is enforced and documented rather than implied.
- `model_config = ConfigDict(extra="forbid")` on request models: unknown fields are a client bug, not something to ignore.
- Validate semantics in the model (`field_validator`, constrained types), not in the handler. `Annotated[int, Field(ge=1, le=100)]` beats an `if` in the body.
- Use `Field(default_factory=...)` for mutable defaults — a bare `[]` or `{}` is shared across instances.

## Dependencies

- `Depends` is the seam: session, current user, settings, clients. That makes them overridable in tests with `app.dependency_overrides` instead of patching.
- One cached `Settings` object (`@lru_cache` on a function returning `BaseSettings`), read from the environment. Never read `os.environ` inside a handler.
- Authorise inside a dependency, not at the top of each handler, so a new route cannot forget it.
- Raise `HTTPException` (or a registered exception handler) for expected failures; let unexpected ones hit one global handler that logs with a traceback and returns a generic 500.

## Async correctness

- A route declared `async def` must not block: no `requests`, no `time.sleep`, no synchronous DB driver, no large synchronous file read. One blocking call stalls every concurrent request in the process.
- Use `httpx.AsyncClient` (one long-lived client in app state, not per request), an async driver, or `await asyncio.to_thread(...)` for unavoidable sync work.
- A purely synchronous handler can stay `def` — FastAPI runs it in a threadpool, which is safer than a blocking `async def`.
- `asyncio.gather` for independent calls; a semaphore to bound fan-out; an explicit timeout on every outbound request.
- Create clients and pools in the lifespan handler and close them there — not at import time.

## Operations

- Background work that must not be lost goes to a real queue, not `BackgroundTasks`. `BackgroundTasks` dies with the process.
- Version the API path (`/v1`) from the first release, and paginate every list endpoint with a capped page size.
- Keep the generated OpenAPI honest: response models, status codes, and `summary`/`description` on each route.
