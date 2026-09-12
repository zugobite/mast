---
description: Acme API conventions — HTTP client, schemas and clinic-local times
globs: "src/**,app/**,api/**,routers/**"
alwaysApply: true
---

# Acme APIs

- All outbound HTTP goes through `acme_http.client()`, which adds `X-Tenant-Id` and auth. No raw `httpx` / `requests` in routers.
- Request and response models live in `schemas/`; never return an ORM object from a route.
- Format clinic-local times with `format_clinic_time(instant, clinic)` — never `datetime.strftime` in a handler.
