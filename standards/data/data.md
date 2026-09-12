---
description: Data and analytics standards — pandas, pipelines, correctness and reproducibility
globs: ["**/*.py"]
alwaysApply: true
---

# Data engineering and analysis

## Correctness first

- Declare the schema you expect and validate on ingest (explicit `dtype`, `pandera`/`pydantic`, or an assertion module that raises). A pipeline that silently accepts a changed column is a pipeline that publishes wrong numbers.
- Never let pandas infer types for money, ids or dates: parse ids as strings (leading zeros, overflow), money as `Decimal` or integer minor units, dates with an explicit format and timezone.
- Handle missing data explicitly and document the choice. Silent `NaN` propagation ends in a report full of blanks nobody questions.
- Assert row counts and key uniqueness around every join, and name the join type you mean. A silent many-to-many join is the most common source of inflated totals.
- Make every transform a pure function of its inputs, returning a new frame, so it can be unit-tested on a ten-row fixture.

## Efficiency

- Vectorise. No `iterrows`, `itertuples` or `apply(axis=1)` in a hot path — they are Python per row and often 100× slower than the column operation.
- Never grow a frame in a loop (`concat`/`append` per iteration copies everything). Collect the parts and concatenate once.
- Avoid chained assignment (`df[mask]["col"] = ...`); use `.loc[mask, "col"] = ...` so the write lands where you think it does.
- Read only the columns and rows you need (`usecols`, predicate pushdown, partitioned Parquet), and process in chunks when the data does not fit comfortably in memory.
- Prefer Parquet over CSV for anything intermediate: typed, compressed and far faster.
- Categoricals for low-cardinality strings; downcast numerics when memory is the constraint.

## Pipelines

- Idempotent tasks: running the same task twice produces the same result. Write to a temporary location and swap, so a failed run never leaves partial output.
- Partition output by the natural time or entity key, and make backfill a parameter rather than a separate code path.
- Separate extract, transform and load so each can be tested and retried independently. Keep credentials and paths in configuration, never inline.
- Log row counts and the time window processed for every stage — those two numbers answer most "is the data wrong?" questions.
- Pin library versions and record them with the output; a silent pandas upgrade changes results.

## Notebooks

- Notebooks are for exploration. Anything scheduled, shared or depended on moves into a module with tests; import the module from the notebook.
- No credentials, no absolute local paths, no hidden state: a notebook must run top to bottom in a fresh kernel.
