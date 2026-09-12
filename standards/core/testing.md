---
description: Testing standards — pytest structure, what to assert, what to never mock
globs: ["**/*.py"]
alwaysApply: true
---

# Testing

## What must have a test

- Every bug fix gets a regression test that fails without the fix. No test, no fix.
- Every behaviour change updates or adds a test that asserts the new behaviour.
- Branches that a reviewer would worry about: empty input, boundary values, the error path, concurrent or repeated calls, timezone and rounding edges.

## Structure

- pytest, plain functions, no test classes unless grouping fixtures demands it.
- Name tests after the behaviour: `test_refund_rejects_amount_above_charge`. A name that needs the body to be understood is a bad name.
- Arrange–act–assert, visible as three blocks. One behaviour per test.
- `tests/` mirrors the package layout; file names are `test_<module>.py` so the tooling (and mast's QA gate) can find the tests covering a change.
- Use fixtures for setup, `tmp_path` for files, `monkeypatch` for environment, `freezegun`/`time-machine` (or an injected clock) for time. Never `time.sleep` in a test.
- `@pytest.mark.parametrize` instead of copy-pasted cases; the parameter ids are documentation.

## What to assert

- Assert behaviour and output, not implementation. A test that asserts a private method was called breaks on every refactor and catches no bugs.
- Assert on one specific thing, with the real expected value. `assert result` passes for almost anything.
- Test the public API of the module. If a private function needs its own test, it probably wants to be public or extracted.
- Use `pytest.raises(SpecificError, match=...)` for error paths — catching `Exception` would pass on a typo.

## Isolation

- No network, no real clock, no shared database state. Tests must pass in any order and in parallel (`pytest -p xdist`), which means no module-level mutable state.
- Mock at the boundary you own (your repository/gateway class), not deep inside a third-party library. Mocking internals freezes someone else's implementation into your test.
- Prefer a real in-memory or container-backed dependency over a mock when the interaction is the thing being tested (SQL, serialisation, migrations).
- No focused or silently skipped tests left behind: no stray `pytest.mark.only`-style filters, and every `skip`/`xfail` carries a reason.
- Coverage is a diagnostic, not a target — but code with no test for its error path is not finished.
