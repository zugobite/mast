---
description: Efficiency standards — algorithmic cost, database access, memory and async
globs: ["**/*.py"]
alwaysApply: true
---

# Efficiency

Most Python performance problems are not the language. They are an accidental O(n²), a query in a
loop, or a blocking call on the event loop — all of which the analyzer finds.

## Algorithmic cost

- Know the cost of the container: `in` on a list is O(n), on a set or dict O(1). Build a set before the loop rather than scanning a list inside it.
- Never build a string by `+=` in a loop. Collect the parts and `"".join(parts)` once.
- Never grow a DataFrame (or any large structure) inside a loop — collect the pieces and concatenate once.
- Hoist work that does not change out of the loop: compiled regexes, configuration lookups, connections, `deepcopy`.
- Stream instead of materialising: generators, `yield`, `itertools`, chunked reads. `sum(x for x in …)` rather than `sum([...])`.
- Measure before optimising anything subtle — `time.perf_counter`, `cProfile`, `pytest-benchmark`. Put the number in the commit message.

## Database and I/O

- One query per loop iteration is the N+1 problem and the most common cause of a slow endpoint. Fetch in one query (`id__in=…`, a join, `select_related`/`selectinload`) and index the result in memory.
- Select the columns you need; paginate every list endpoint; put an index behind every filter you ship.
- Batch writes (`bulk_create`, `executemany`) and keep transactions short. Never hold a transaction open across a network call.
- Cache deliberately: a named key, an explicit TTL, and a documented invalidation. An unbounded in-process cache is a memory leak — and `lru_cache` on a method keeps every instance alive.

## Async

- Nothing blocking inside `async def`: no `time.sleep`, no `requests`, no synchronous driver, no large synchronous file read. One blocking call freezes every request in the process.
- Use the async client, or push the work to a thread with `await asyncio.to_thread(...)`.
- Independent awaits run together with `asyncio.gather`; a sequential loop of awaits pays the sum of every round trip.
- Bound concurrency (a semaphore, a pool size) so a burst cannot exhaust connections or memory, and always set timeouts.

## Memory

- Process large inputs in chunks; do not read a whole file or query result into a list when a generator will do.
- `__slots__` (or a slotted dataclass) for objects you create by the million.
- Release what you hold: close clients, clear caches with a lifetime, avoid accumulating state across requests.
