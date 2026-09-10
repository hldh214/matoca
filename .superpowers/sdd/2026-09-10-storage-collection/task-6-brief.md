### Task 6: Retention, Documentation, and Phase Verification

**Files:**
- Modify: `src/matoca_service/storage/repositories.py`
- Modify: `src/matoca_service/storage/migrations.py`
- Modify: `src/matoca_service/storage/models.py`
- Modify: `src/matoca_service/collection/coordinator.py`
- Modify: `README.md`
- Test: `tests/unit/storage/test_retention.py`
- Modify: `tests/unit/storage/test_database.py`

**Interfaces:**
- Produces: `ShopRepository.rollup_and_prune(now: datetime) -> RetentionResult`.
- Consumes: migration-2 observations and the migration-4 rollup table.

Migration: Task 6 adds migration version 4 for retention rollups. The unimplemented
prediction and Web Push migrations are reserved as versions 5 and 6.

- [ ] **Step 1: Write a failing transactional retention test**

```python
def test_rollup_commits_before_raw_rows_are_deleted(database: Database) -> None:
    repository = ShopRepository(database)
    repository.save_cycle(cycle_at(now - timedelta(days=181), waiting=10))
    repository.save_cycle(cycle_at(now - timedelta(days=179), waiting=8))

    result = repository.rollup_and_prune(now)

    assert result.raw_deleted == 1
    assert repository.rollups("sawayaka", 3272)[0].sample_count == 1
    assert len(repository.observations("sawayaka", 3272, limit=10)) == 1
```

- [ ] **Step 2: Run the retention test and confirm failure**

Run: `uv run pytest tests/unit/storage/test_retention.py -v`

Expected: FAIL because `rollup_and_prune` does not exist.

- [ ] **Step 3: Implement five-minute aggregation and deletion in one transaction**

Aggregate count, minimum, maximum, and average for waiting groups and waiting minutes. Only aggregate rows where the corresponding metric was freshly observed. Delete source rows older than `now - timedelta(days=180)` after the aggregate upsert succeeds in the same transaction. Run maintenance at most once per local day.

- [ ] **Step 4: Update README runtime and storage sections**

Replace the statement that no database is required. Document `MATOCA_DATABASE_FILE`, `data/`, SQLite/WAL permissions, adaptive polling request behavior, 180-day raw retention, and that merchant definitions remain tracked in `merchant_registry.toml`.

- [ ] **Step 5: Run the full quality gate**

Run: `uv run pytest`

Expected: PASS.

Run: `uv run ruff check src tests`

Expected: PASS.

Run: `uv run ruff format --check src tests`

Expected: PASS.

Run: `uv run mypy src`

Expected: PASS.

Run: `uv build`

Expected: wheel and source distribution build successfully without runtime data.

- [ ] **Step 6: Commit**

```bash
git add README.md src/matoca_service/storage src/matoca_service/collection tests/unit/storage
git commit -m "feat: retain queue observation history"
```
