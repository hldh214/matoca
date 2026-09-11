# Storage and Collection Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the disposable JSON shop cache with a private SQLite store and collect complete shop snapshots at the agreed adaptive cadence.

**Architecture:** Keep `MatocaService` as the authenticated upstream facade, add a typed SQLite repository, and run one collection coordinator from FastAPI lifespan. Each cycle fetches paginated shop lists plus bounded-concurrency detail reads, persists fresh/null fields without inventing data, and serves the latest database snapshot to the Web API.

**Tech Stack:** CPython 3.14, stdlib `sqlite3`, `asyncio`, FastAPI lifespan, HTTPX, Pydantic v2, pytest

**Spec:** `docs/designs/2026-09-10-queue-automation-design.md`

## Global Constraints

- Use `uv`; do not use system Python or `pip`.
- Keep one process and one Uvicorn worker under Supervisor.
- Keep all visible Web UI text Japanese.
- Keep tokens only in `state.json`; never persist or log them in SQLite.
- Create the runtime data directory with mode `0700` and database/WAL/SHM files with mode `0600`.
- Never perform a real queue POST, DELETE, or forced Native Refresh during implementation or tests.
- Preserve the existing LINE/LIFF protocol implementation and one-retry 401/403 behavior.
- Fetch shop detail with concurrency four; skip overlapping cycles and honor HTTP 429 backoff.
- Store timestamps in UTC and retain raw observations for 180 days.

---

### Task 1: Runtime Database Path and SQLite Bootstrap

**Files:**
- Create: `src/matoca_service/storage/__init__.py`
- Create: `src/matoca_service/storage/database.py`
- Create: `src/matoca_service/storage/migrations.py`
- Modify: `src/matoca_service/config.py`
- Modify: `.env.example`
- Modify: `.gitignore`
- Test: `tests/unit/storage/test_database.py`
- Modify: `tests/unit/test_project_contract.py`

**Interfaces:**
- Produces: `Database(path: Path)`, `Database.initialize() -> None`, `Database.read(operation: Callable[[sqlite3.Connection], T]) -> T`, and `Database.write(operation: Callable[[sqlite3.Connection], T]) -> T`.
- Produces: `RuntimeSettings.database_file: Path`, defaulting to `Path("data/matoca.db")`.
- Consumes: no application services.

- [ ] **Step 1: Write failing database permission and migration tests**

```python
def test_initialize_creates_private_database_and_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "matoca.db")

    database.initialize()

    assert stat.S_IMODE(database.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.path.stat().st_mode) == 0o600
    assert database.read(lambda connection: connection.execute("PRAGMA user_version").fetchone()[0]) == 1


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "matoca.db")
    database.initialize()
    database.initialize()

    assert database.read(lambda connection: connection.execute("PRAGMA journal_mode").fetchone()[0]) == "wal"
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `uv run pytest tests/unit/storage/test_database.py -v`

Expected: FAIL because `matoca_service.storage.database` does not exist.

- [ ] **Step 3: Implement the database wrapper and migration runner**

```python
class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        with self._connect() as connection:
            migrate(connection)
        self._enforce_file_modes()

    def read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with self._connect() as connection:
            return operation(connection)

    def write(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with self._connect() as connection:
            with connection:
                result = operation(connection)
        self._enforce_file_modes()
        return result
```

`_connect()` must set `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`, and initialize WAL once. Migration version 1 establishes the database bootstrap metadata. `migrate()` applies ordered migration functions transactionally and advances `user_version` only after each migration succeeds.

- [ ] **Step 4: Add the runtime path and ignore contract**

```python
class RuntimeSettings(BaseSettings):
    database_file: Path = Path("./data/matoca.db")
```

Add `MATOCA_DATABASE_FILE=./data/matoca.db` to `.env.example`, add `data/` to `.gitignore`, and assert both `data/` and `.superpowers/` in `test_mutable_files_are_gitignored`.

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest tests/unit/storage/test_database.py tests/unit/test_project_contract.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .env.example .gitignore src/matoca_service/config.py src/matoca_service/storage tests/unit/storage tests/unit/test_project_contract.py
git commit -m "feat: add private sqlite storage"
```

### Task 2: Shop, Observation, and Preference Repositories

**Files:**
- Create: `src/matoca_service/storage/models.py`
- Create: `src/matoca_service/storage/repositories.py`
- Modify: `src/matoca_service/storage/migrations.py`
- Test: `tests/unit/storage/test_repositories.py`

**Interfaces:**
- Produces: `StoredShop`, `ShopObservation`, `CollectionWrite`, `UserPreferences`, and `MerchantPollState` dataclasses.
- Produces: `ShopRepository.save_cycle(cycle: CollectionWrite) -> None`, `ShopRepository.latest(merchant_key: str) -> list[StoredShop]`, `ShopRepository.poll_window(merchant_key: str, now: datetime) -> PollWindow | None`.
- Produces: `PreferenceRepository.get() -> UserPreferences` and `PreferenceRepository.update(preferences: UserPreferences) -> UserPreferences`.
- Consumes: `Database` from Task 1.

- [ ] **Step 1: Write failing repository tests for one-minute uniqueness and fresh/null detail values**

```python
def test_save_cycle_replaces_same_minute_without_copying_stale_detail(database: Database) -> None:
    repository = ShopRepository(database)
    observed_at = datetime(2026, 9, 10, 8, 1, 40, tzinfo=UTC)
    repository.save_cycle(CollectionWrite(
        merchant_key="sawayaka",
        observed_at=observed_at,
        shops=[observation_shop(waiting_minutes=25, detail_fresh=True)],
    ))
    repository.save_cycle(CollectionWrite(
        merchant_key="sawayaka",
        observed_at=observed_at.replace(second=55),
        shops=[observation_shop(waiting_minutes=None, detail_fresh=False, error_code="timeout")],
    ))

    rows = repository.observations("sawayaka", 3272, limit=10)
    assert len(rows) == 1
    assert rows[0].waiting_minutes is None
    assert rows[0].detail_fresh is False
```

- [ ] **Step 2: Write failing preference-default test**

```python
def test_preferences_default_to_two_adults_and_fifteen_minute_budgets(database: Database) -> None:
    preferences = PreferenceRepository(database).get()

    assert preferences.default_adult_count == 2
    assert preferences.default_child_count == 0
    assert preferences.early_tolerance_minutes == 15
    assert preferences.model_error_minutes == 15
```

- [ ] **Step 3: Run repository tests and confirm failure**

Run: `uv run pytest tests/unit/storage/test_repositories.py -v`

Expected: FAIL because the repository types do not exist.

- [ ] **Step 4: Add migration version 2 tables**

Create structured `shops`, `shop_observations`, `merchant_poll_state`, and `preferences` tables. Use this observation key and index:

```sql
PRIMARY KEY (merchant_key, shop_id, observed_minute)
CREATE INDEX shop_observations_history
ON shop_observations (merchant_key, shop_id, observed_minute DESC);
```

Observation fields must include `current_waiting`, nullable `waiting_minutes`, `waiting_is_more`, nullable `is_open`, nullable `is_issuable`, `is_holiday`, `is_suspended`, `list_fresh`, `detail_fresh`, and `error_code`.

- [ ] **Step 5: Implement typed repository conversions and one-transaction `save_cycle`**

```python
@dataclass(frozen=True)
class UserPreferences:
    default_adult_count: int = 2
    default_child_count: int = 0
    early_tolerance_minutes: int = 15
    model_error_minutes: int = 15


class ShopRepository:
    def save_cycle(self, cycle: CollectionWrite) -> None:
        self._database.write(lambda connection: self._save_cycle(connection, cycle))
```

Normalize `observed_minute` to UTC with seconds and microseconds zeroed. Use one upsert for cached shop identity and one upsert for the observation. Store Pydantic payload fragments as canonical JSON only where a nested upstream structure cannot be queried usefully.

- [ ] **Step 6: Run repository tests**

Run: `uv run pytest tests/unit/storage/test_repositories.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/storage tests/unit/storage/test_repositories.py
git commit -m "feat: persist shop observations"
```

### Task 3: Complete Merchant Collection Cycle

**Files:**
- Create: `src/matoca_service/collection/__init__.py`
- Create: `src/matoca_service/collection/models.py`
- Create: `src/matoca_service/collection/service.py`
- Modify: `src/matoca_service/service.py`
- Test: `tests/unit/collection/test_service.py`
- Modify: `tests/unit/test_service.py`

**Interfaces:**
- Produces: `CollectedShop`, `CollectionCycle`, and `CollectionService.collect(merchant_key: str) -> CollectionCycle`.
- Adds: `MatocaService.read_collection_cycle(merchant_key: str) -> CollectionCycle`, which authenticates once and performs list/detail/waiting reads under the existing operation lock.
- Consumes: `ShopRepository.save_cycle()` from Task 2 and existing `MatocaClient.list_all_shops()`, `get_shop()`, and `list_waiting()`.

- [ ] **Step 1: Write a failing test proving the list result is enriched with concurrency-limited details**

```python
@pytest.mark.asyncio
async def test_collect_enriches_every_list_shop_and_persists_one_cycle() -> None:
    upstream = FakeMerchantReader(
        base_shops=[Shop(id=1, name="A", current_waiting=10), Shop(id=2, name="B", current_waiting=4)],
        details={1: Shop(id=1, name="A", current_waiting=10, is_open=True, is_issuable=True),
                 2: Shop(id=2, name="B", current_waiting=4, is_open=True, is_issuable=True)},
    )
    repository = RecordingShopRepository()

    cycle = await CollectionService(upstream, repository, now=fixed_now).collect("sawayaka")

    assert [item.shop.id for item in cycle.shops] == [1, 2]
    assert all(item.detail_fresh for item in cycle.shops)
    assert repository.saved == [cycle]
```

- [ ] **Step 2: Write a failing partial-detail test**

```python
@pytest.mark.asyncio
async def test_collect_marks_failed_detail_null_and_keeps_list_waiting_count() -> None:
    upstream = FakeMerchantReader(detail_error=httpx.ReadTimeout("timeout"))

    cycle = await CollectionService(upstream, RecordingShopRepository(), now=fixed_now).collect("sawayaka")

    item = cycle.shops[0]
    assert item.shop.current_waiting == 10
    assert item.shop.waiting_time is None
    assert item.detail_fresh is False
    assert item.error_code == "timeout"
```

- [ ] **Step 3: Run the collection tests and confirm failure**

Run: `uv run pytest tests/unit/collection/test_service.py -v`

Expected: FAIL because the collection package does not exist.

- [ ] **Step 4: Implement the cycle models and service**

```python
@dataclass(frozen=True)
class CollectedShop:
    shop: Shop
    list_fresh: bool
    detail_fresh: bool
    error_code: str | None = None


class CollectionService:
    async def collect(self, merchant_key: str) -> CollectionCycle:
        cycle = await self._reader.read_collection_cycle(merchant_key)
        await asyncio.to_thread(self._repository.save_cycle, cycle.to_storage())
        return cycle
```

In `MatocaService.read_collection_cycle`, fetch `list_all_shops()` first, then use `asyncio.Semaphore(4)` around each `get_shop()`. Merge list `current_waiting` and identity fields into the successful detail model. Re-raise 401/403 for the existing LIFF refresh path. Map timeout, transport, malformed response, 429, and other HTTP status failures to stable non-secret error codes.

- [ ] **Step 5: Run focused collection and service tests**

Run: `uv run pytest tests/unit/collection/test_service.py tests/unit/test_service.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/matoca_service/collection src/matoca_service/service.py tests/unit/collection tests/unit/test_service.py
git commit -m "feat: collect complete merchant snapshots"
```

### Task 4: Adaptive Poll Schedule and Backoff

Migration: Task 4 adds migration version 3 for durable merchant poll backoff state.

**Files:**
- Create: `src/matoca_service/collection/schedule.py`
- Create: `src/matoca_service/collection/coordinator.py`
- Modify: `src/matoca_service/storage/repositories.py`
- Test: `tests/unit/collection/test_schedule.py`
- Test: `tests/unit/collection/test_coordinator.py`

**Interfaces:**
- Produces: `PollSchedule.next_interval(merchant_key: str, now: datetime, has_active_task: bool) -> timedelta`.
- Produces: `CollectionCoordinator.run_once() -> None`, `run() -> None`, and `stop() -> None`.
- Consumes: `ShopRepository.poll_window()`, `MerchantRegistry`, and `CollectionService.collect()`.

- [ ] **Step 1: Write failing schedule tests**

```python
def test_new_merchant_polls_every_five_minutes() -> None:
    assert PollSchedule(repository_without_history).next_interval("new", now, False) == timedelta(minutes=5)


def test_known_business_window_polls_every_minute() -> None:
    repository = repository_with_window(time(10, 30), time(23, 0))
    assert PollSchedule(repository).next_interval("sawayaka", at_local_time(12, 0), False) == timedelta(minutes=1)


def test_outside_window_polls_every_fifteen_minutes() -> None:
    repository = repository_with_window(time(10, 30), time(23, 0))
    assert PollSchedule(repository).next_interval("sawayaka", at_local_time(3, 0), False) == timedelta(minutes=15)
```

- [ ] **Step 2: Write failing coordinator overlap and 429 tests**

```python
@pytest.mark.asyncio
async def test_run_once_does_not_start_second_cycle_for_busy_merchant() -> None:
    collector = BlockingCollector()
    coordinator = CollectionCoordinator(registry, collector, schedule, repository)
    first = asyncio.create_task(coordinator.run_once())
    await collector.started.wait()

    await coordinator.run_once()

    assert collector.calls == ["sawayaka"]
    collector.release.set()
    await first


@pytest.mark.asyncio
async def test_429_sets_merchant_retry_deadline() -> None:
    collector = FailingCollector(CollectionRateLimited(retry_after=timedelta(minutes=3)))
    await coordinator_for(collector).run_once()
    assert repository.poll_state("sawayaka").retry_at == now + timedelta(minutes=3)
```

- [ ] **Step 3: Run focused tests and confirm failure**

Run: `uv run pytest tests/unit/collection/test_schedule.py tests/unit/collection/test_coordinator.py -v`

Expected: FAIL because schedule and coordinator modules do not exist.

- [ ] **Step 4: Implement historical window calculation**

Query the preceding 30 local calendar days. The effective window is the earliest minute with any fresh `is_open=1` and the latest such minute, expanded by 30 minutes on both sides. If the expanded range crosses midnight, represent it explicitly rather than comparing naive times.

Return five minutes when no window exists, one minute inside the window or during an active task, and 15 minutes otherwise.

- [ ] **Step 5: Implement coordinator lifecycle and backoff**

```python
class CollectionCoordinator:
    async def run(self) -> None:
        while not self._stop_event.is_set():
            await self.run_once()
            await self._wait_until_next_due()

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
```

Keep one in-flight task per merchant. A missing `Retry-After` uses 1, 2, 4, 8, then 15 minutes, resetting after success. Persist `last_attempt_at`, `last_success_at`, `retry_at`, and the stable error code; never persist exception strings containing response data.

- [ ] **Step 6: Run schedule and coordinator tests**

Run: `uv run pytest tests/unit/collection/test_schedule.py tests/unit/collection/test_coordinator.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/collection src/matoca_service/storage tests/unit/collection
git commit -m "feat: schedule adaptive shop collection"
```

### Task 5: Cached Snapshot Service and FastAPI Lifespan

**Files:**
- Modify: `src/matoca_service/service.py`
- Modify: `src/matoca_service/web/app.py`
- Delete: `src/matoca_service/catalog.py`
- Delete: `tests/unit/test_catalog.py`
- Modify: `tests/unit/web/test_app.py`
- Modify: `tests/unit/test_service.py`
- Test: `tests/unit/web/test_lifespan.py`

**Interfaces:**
- Changes: `MatocaService.__init__(line_client_path: Path, state_path: Path, database_path: Path)`.
- Changes: `merchant_snapshot()` reads the latest stored cycle and only performs upstream I/O when no stored cycle exists or `force_catalog=True`.
- Consumes: `Database`, `ShopRepository`, `CollectionService`, and `CollectionCoordinator`.

- [ ] **Step 1: Write a failing test that cached snapshots avoid upstream reads**

```python
@pytest.mark.asyncio
async def test_merchant_snapshot_uses_database_without_upstream_request(stored_service: MatocaService) -> None:
    snapshot = await stored_service.merchant_snapshot("sawayaka")

    assert snapshot.shops[0].id == 3272
    assert snapshot.refreshed_at == datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    assert upstream_calls == []
```

- [ ] **Step 2: Write failing lifespan start/stop test**

```python
@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_collection_coordinator() -> None:
    coordinator = RecordingCoordinator()
    app = create_app(FakeDashboardService(), collection_coordinator=coordinator)

    async with app.router.lifespan_context(app):
        assert coordinator.started is True

    assert coordinator.stopped is True
```

- [ ] **Step 3: Run focused tests and confirm failure**

Run: `uv run pytest tests/unit/web/test_lifespan.py tests/unit/web/test_app.py tests/unit/test_service.py -v`

Expected: FAIL because stored snapshots and injected coordinator lifespan are not implemented.

- [ ] **Step 4: Replace JSON catalog reads with repository reads**

Build `MerchantSnapshot` from the latest per-shop stored records. Set `stale=True` when the latest complete detail timestamp is older than two polling intervals or the last cycle is partial. Keep the current manual refresh route, but make it execute `CollectionService.collect()` rather than reviving the JSON cache.

Remove `ShopCatalogStore`, `shop_cache_file`, `MATOCA_SHOP_CACHE_FILE`, and their tests only after all call sites use SQLite.

- [ ] **Step 5: Add injected lifespan coordination**

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    coordinator.start()
    try:
        yield
    finally:
        await coordinator.stop()
```

When `create_app()` receives a fake service and no coordinator, use a no-op lifespan so import and route tests never open runtime files or start network polling.

- [ ] **Step 6: Run focused and complete tests**

Run: `uv run pytest tests/unit/web/test_lifespan.py tests/unit/web/test_app.py tests/unit/test_service.py -v`

Expected: PASS.

Run: `uv run pytest`

Expected: PASS with no live/destructive marker executed.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service tests
git commit -m "feat: serve cached merchant snapshots"
```

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

Migration: Task 6 adds migration version 4 for retention rollups. The final review
fix adds version 5 for catalog membership, completeness, daily static refresh timing,
and the polling-window index. The unimplemented prediction and Web Push migrations
are reserved as versions 6 and 7. Versions 1–4 remain unchanged and upgrade additively.

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
