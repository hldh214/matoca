# Task 6 Report: Retention, Documentation, and Phase Verification

## Plan Correction

The task brief described a migration-2 rollup table, but the committed schema had
only raw observations in migration 2 and a deployed poll-state migration 3. Per the
authorized ruling, this task adds the rollup table as additive migration 4. The
unimplemented prediction and Web Push migrations are now documented as versions 5
and 6; no prediction or Web Push behavior was implemented.

## Implementation

- Added migration 4 with `shop_observation_rollups_5m` and its history index.
- Added immutable `RetentionResult` and `ObservationRollup` storage models.
- Added `ShopRepository.rollup_and_prune(now)` and `rollups()`. Each maintenance
  invocation uses one SQLite write transaction: aggregate eligible raw observations,
  upsert/merge rollups, delete raw rows older than 180 days, and record the Tokyo-local
  maintenance day. Fresh list data supplies waiting-group aggregates; fresh non-null
  detail data supplies waiting-time aggregates.
- Added an in-memory Tokyo-local-day coordinator guard. The repository metadata guard
  also prevents repeat maintenance after process restart.
- Added the required README runtime/storage documentation and renumbered the tracked
  prediction and Web Push migration plans to versions 5 and 6.

## Files

- Modified: `README.md`
- Modified: `src/matoca_service/storage/migrations.py`
- Modified: `src/matoca_service/storage/models.py`
- Modified: `src/matoca_service/storage/repositories.py`
- Modified: `src/matoca_service/collection/coordinator.py`
- Modified: `tests/unit/storage/test_database.py`
- Modified: `tests/unit/collection/test_coordinator.py`
- Added: `tests/unit/storage/test_retention.py`
- Modified: storage, prediction, and Web Push planning documents plus the SDD ledger.

## RED

```text
uv run pytest tests/unit/storage/test_retention.py tests/unit/storage/test_database.py -v
```

Result: 7 failed and 2 passed. The retention tests failed with
`AttributeError: 'ShopRepository' object has no attribute 'rollup_and_prune'`; the
migration tests observed version 3 instead of the required version 4.

```text
uv run pytest tests/unit/collection/test_coordinator.py::test_run_once_runs_retention_once_per_tokyo_day -v
```

Result: 1 failed. No retention calls were scheduled before the coordinator change.

## GREEN

```text
uv run pytest tests/unit/storage/test_retention.py tests/unit/storage/test_database.py tests/unit/collection/test_coordinator.py -v
```

Result: 22 passed.

During GREEN, SQLite reported `near "DO": syntax error` for `INSERT ... SELECT ...
ON CONFLICT`. A minimal isolated SQLite reproduction confirmed that `ON` after a
`FROM` clause is parsed as a possible join unless the select is terminated. Adding
`WHERE 1` before `ON CONFLICT` fixed that parser ambiguity; the focused suite then
passed.

## Full Verification

```text
uv run pytest
117 passed in 3.96s

uv run ruff check src tests
All checks passed!

uv run ruff format --check src tests
54 files already formatted

uv run mypy src
Success: no issues found in 31 source files

uv build
Successfully built dist/matoca_service-0.1.0.tar.gz
Successfully built dist/matoca_service-0.1.0-py3-none-any.whl
```

## Self-Review

- Migration 4 is additive and the exact v3-to-v4 upgrade path is covered.
- The aggregate upsert precedes raw deletion in the same `Database.write()`
  transaction. A failure rolls back both the rollup write and deletion.
- Rollup merging preserves count, minimum, maximum, and weighted average when a
  five-minute bucket is split across maintenance runs.
- Both the coordinator and SQLite metadata use the Asia/Tokyo local date, enforcing
  at most one maintenance run per local day across calls and restarts.
- README paths are repository-relative and contain no machine-specific location.
- No credentials, tokens, real state files, upstream queue mutations, or forced
  Native refreshes were accessed or added.

## Concerns

None. The expected `dist/` build output remains ignored and untracked.
