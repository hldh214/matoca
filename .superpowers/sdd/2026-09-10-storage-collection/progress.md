# SDD ledger — plan: docs/plans/2026-09-10-storage-collection.md

Baseline: master at a8b280f; `uv run pytest` passed 64/64 on CPython 3.14.7.
Workspace: working in place by prior explicit user direction to develop on master; repository was clean.

## Preflight Interface Scan

| Tasks | Shared file/interface | Finding |
| --- | --- | --- |
| 1 + 2 | `storage/migrations.py`, `Database` | Conflict: Task 1 expected user_version 1 while Task 2 also called its tables migration 1. Ruled below. |
| 2 + 3 | `ShopRepository.save_cycle(CollectionWrite)` | Clean: Task 2 produces the storage write consumed by Task 3. |
| 2 + 4 | `ShopRepository.poll_window`, poll state | Clean: Task 2 produces history/poll state; Task 4 adds scheduling writes. |
| 2 + 5 | `ShopRepository.latest` | Clean: repository output becomes the cached snapshot source. |
| 2 + 6 | observation/rollup tables, `rollup_and_prune` | Clean: Task 6 extends the Task 2 repository and schema. |
| 3 + 4 | `CollectionService.collect` | Clean: Task 4 schedules Task 3 without changing its contract. |
| 3 + 5 | `CollectionService`, `MatocaService.read_collection_cycle` | Clean: Task 5 assembles already-defined components. |
| 4 + 5 | `CollectionCoordinator` and FastAPI lifespan | Clean: Task 5 owns lifecycle integration after Task 4 defines lifecycle methods. |
| 4 + 6 | coordinator maintenance call | Clean: Task 6 adds daily retention scheduling to the existing loop. |
| 5 + 6 | README/runtime storage behavior | Clean: Task 6 documents the final phase behavior after Task 5 removes JSON cache. |
| 1 | Tests vs implementation/files | Clean after ruling: bootstrap migration v1, permissions, settings, ignores all have implementation steps. |
| 2 | Tests vs implementation/files | Clean after ruling: business tables become migration v2; typed repositories match tests. |
| 3 | Tests vs implementation/files | Clean: partial failure, fresh/null semantics, bounded detail reads are explicit. |
| 4 | Tests vs implementation/files | Clean: cadence, overlap suppression, persisted 429 backoff and lifecycle align. |
| 5 | Tests vs implementation/files | Clean: cached reads, force collection, JSON cache removal, injected lifespan align. |
| 6 | Tests vs implementation/files | Clean: transactional rollup-before-delete, documentation, and quality gates align. |

Task 1: Ruling: migration numbering conflict — version 1 is a bootstrap migration owned by Task 1; Task 2 creates shop/preference tables in version 2, later plans use versions 3 through 6 — this preserves ordered migration semantics; if wrong, migration numbering and tests require a small coordinated rewrite before deployment.
Task 1: fix round 1/5 (1 addressed, 0 open — enforce database mode after failed migration; commits 72f0520..349a72c)
Task 2: implementation complete (commit 8587d7b, 72 tests passed); task review interrupted at user-requested pause and must be re-dispatched before Task 2 can be marked complete.
Task 1: complete (commits ee7c526..349a72c, review clean)
Task 2: fix round 1/5 (2 addressed, 0 open — atomic migration and Tokyo-local poll window; commits 8587d7b..2a69dec)
Task 2: complete (commits 349a72c..2a69dec, review clean)
Task 3: fix round 1/5 (1 addressed, 1 open — first-attempt drain fixed; simultaneous auth errors still blocked retry; commits 3fead39..300bfe2)
Task 3: fix round 2/5 (1 addressed, 0 open — bare auth retry preserved after full drain; commits 300bfe2..aa72a2e)
Task 3: complete (commits 2a69dec..aa72a2e, review clean)
Task 4: fix round 1/5 (2 addressed, 1 open — retry deadline now uses failure time and the backoff rung is durable; existing v2 databases still lacked the new column; commits 889595c..0ca603a)
Task 4: Ruling: committed migration v2 must remain upgradeable — add migration v3 for `failure_count` and move the prediction and Web Push migrations to v4 and v5; this preserves deployed databases, while an incorrect assumption would require renumbering the later unreleased migrations before their implementation.
Task 4: fix round 2/5 (1 addressed, 0 open — legacy v2 databases upgrade through migration v3; commits 0ca603a..26edf9d)
Task 4: complete (commits aa72a2e..26edf9d, review clean)
Task 5: fix round 1/5 (2 addressed, 0 open — snapshot staleness now follows twice the applicable poll interval with dynamic boundary coverage; commits 48a71fd..009529a)
Task 5: complete (commits 26edf9d..009529a, review clean)
Task 6: Plan defect: the brief described a migration-v2 rollup table, but the committed v2 schema has only raw observations and v3 is already deployed for poll failure counts. Ruling: add the retention rollup table as migration v4, preserve upgrade compatibility, and shift the unimplemented prediction and Web Push migrations to v5 and v6.
Task 6: Ruling: the plan incorrectly says Task 6 consumes a migration-v2 rollup table that does not exist — add the rollup table in a new migration v4 and move the still-unimplemented prediction and Web Push migrations to v5 and v6; this preserves every deployed v2/v3 upgrade path, while an incorrect ruling would require renumbering only unreleased future migrations.
