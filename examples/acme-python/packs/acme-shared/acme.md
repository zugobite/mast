---
description: Acme — company context, cross-repo contracts and hard stops shared by every Acme Python repo
alwaysApply: true
---

# Acme Python standards

Acme builds a scheduling product for clinics. When a repo's own docs are more specific than this, the repo wins.

## Contracts shared by every repo

- Every API request carries `X-Tenant-Id`; every query on tenant data filters by it. Cross-tenant access is a security defect.
- Money is stored as integer cents with an ISO currency code — never floats.
- Dates cross boundaries as ISO 8601 UTC strings; convert to the clinic's timezone only for display, with `format_clinic_time`.

## Workflow

- Branch from `main` as `feat/…` / `fix/…`; squash-merge with a Conventional Commit title.
- Every user-visible change gets a `CHANGELOG.md` entry under `## [Unreleased]`.

## Stop and ask when

- a change needs a new dependency, a new environment variable or a schema migration;
- a request would touch another tenant's data.
