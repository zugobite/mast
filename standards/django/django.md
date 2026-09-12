---
description: Django standards — models, queries, views, settings, migrations
globs: ["**/*.py"]
alwaysApply: true
---

# Django

## Models

- Models hold data and invariants; business processes live in a service module, not in fat models or in views.
- Every field choice is deliberate: `null` vs `blank`, `on_delete`, `db_index`, `unique_together`/`UniqueConstraint`, and `DecimalField` (never float) for money.
- `related_name` on every relation. Use `TextChoices`/`IntegerChoices` for enumerations.
- Override `save()` only for data invariants, and keep it side-effect free — no emails, no HTTP calls. Use signals sparingly and never for business logic; an explicit service call is findable, a signal is not.

## Queries

- Avoid N+1: `select_related` for forward FK/one-to-one, `prefetch_related` for reverse and many-to-many. Never query inside a loop.
- `only()`/`defer()`/`values()` when you need a few columns; `exists()` instead of `len(qs)`; `count()` instead of `len(qs)` when you only need the number.
- `bulk_create`/`bulk_update` for batches, `update()` for set-based writes, `F()` and `Q()` instead of read-modify-write races.
- `select_for_update()` inside an explicit `transaction.atomic()` for anything read-then-written concurrently.
- Keep `transaction.atomic()` blocks short and never wrap an external API call; use `transaction.on_commit()` to fire follow-up work.
- No raw SQL without parameters, ever. `.raw()` and `.extra()` take bound parameters.

## Views and serialisation

- Thin views: validate with a form or DRF serializer, call a service, return the response. No ORM queries scattered through templates.
- Permissions on every view and every DRF viewset; scope querysets to the request's user/tenant in `get_queryset`, never filter by an id from the body.
- Paginate every list endpoint. Set `DEFAULT_PAGINATION_CLASS` and a page-size cap.

## Settings and deployment

- Split settings per environment with a shared base; every secret and environment-specific value comes from the environment. `DEBUG` defaults to off, `ALLOWED_HOSTS` is explicit, `SECRET_KEY` is never a literal.
- Keep the security middleware defaults on: HSTS, secure cookies, `SECURE_SSL_REDIRECT`, CSRF for session-authenticated endpoints.
- Migrations are committed with the model change that caused them, are reviewed like code, and never edited after release. Data migrations are idempotent and reversible, and are kept separate from schema migrations.
- `manage.py check --deploy` clean before release.
