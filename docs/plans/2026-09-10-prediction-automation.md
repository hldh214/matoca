# Prediction and Queue Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add transparent queue-call predictions and restart-safe one-time tasks that submit exactly once when the confirmed timing rule is satisfied.

**Architecture:** Store tasks, transitions, queue journeys, and training samples in SQLite; keep the prediction function pure; and run one automation coordinator beside the collection coordinator. Persist `submitting` before any queue POST, reconcile every ambiguous outcome, and never retry an uncertain write blindly.

**Tech Stack:** CPython 3.14, stdlib `sqlite3`, `asyncio`, Pydantic v2, FastAPI, pytest

**Spec:** `docs/designs/2026-09-10-queue-automation-design.md`

## Global Constraints

- Complete `docs/plans/2026-09-10-storage-collection.md` first.
- Use `uv`; do not use system Python or `pip`.
- Keep one process and one Uvicorn worker under Supervisor.
- Keep all visible Web UI and API error text Japanese.
- Never persist or log LINE, LIFF, Push, account, or device credentials in automation records.
- Never issue a real queue POST, DELETE, or forced Native Refresh during tests.
- Use the shop's stored coordinates for queue creation.
- Default to two adults, zero children, 15 minutes early tolerance, and 15 minutes model error.
- Do not predict reception closing time or submit early to beat a possible closure.
- Reject stale or partial detail observations as automation inputs.

---

### Task 1: Automation Schema and Typed Repositories

**Files:**
- Create: `src/matoca_service/automation/__init__.py`
- Create: `src/matoca_service/automation/models.py`
- Create: `src/matoca_service/automation/repository.py`
- Modify: `src/matoca_service/storage/migrations.py`
- Test: `tests/unit/automation/test_repository.py`

**Interfaces:**
- Produces: `TaskState`, `AutomationTask`, `TaskDraft`, `QueueSession`, `QueueSessionObservation`, and `PredictionSample`.
- Produces: `AutomationRepository.create(draft: TaskDraft, now: datetime) -> AutomationTask`, `get(task_id: int) -> AutomationTask`, `list_nonterminal() -> list[AutomationTask]`, `transition(task_id: int, expected: set[TaskState], target: TaskState, code: str, now: datetime) -> AutomationTask`, and queue-session/sample methods.
- Consumes: `Database` from the storage plan.

- [ ] **Step 1: Write failing creation/default and transition tests**

```python
def test_create_task_persists_resolved_defaults(database: Database) -> None:
    task = AutomationRepository(database).create(
        TaskDraft(
            merchant_key="sawayaka",
            shop_id=3272,
            arrival_at=datetime(2026, 9, 10, 9, tzinfo=UTC),
            source_timezone="Asia/Tokyo",
            adult_count=2,
            child_count=0,
            answer1=0,
            answer2=None,
        ),
        now=datetime(2026, 9, 10, 7, tzinfo=UTC),
    )

    assert task.state is TaskState.SCHEDULED
    assert task.early_tolerance_minutes == 15
    assert task.model_error_minutes == 15


def test_transition_is_compare_and_swap(database: Database) -> None:
    repository = AutomationRepository(database)
    task = repository.create(task_draft(), now)
    repository.transition(task.id, {TaskState.SCHEDULED}, TaskState.MONITORING, "monitoring_started", now)

    with pytest.raises(ConcurrentTaskUpdate):
        repository.transition(task.id, {TaskState.SCHEDULED}, TaskState.CANCELLED, "cancelled", now)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `uv run pytest tests/unit/automation/test_repository.py -v`

Expected: FAIL because automation models and repository do not exist.

- [ ] **Step 3: Add migration version 2**

Create `automation_tasks`, `automation_events`, `queue_sessions`, `queue_session_observations`, `prediction_samples`, and `prediction_stats`. Use integer primary keys and UTC ISO-8601 text timestamps. Add indexes for nonterminal task state, merchant/shop observation history, and prediction hierarchy dimensions.

Use a partial unique index so at most one queue session can have a nonterminal state:

```sql
CREATE UNIQUE INDEX one_active_queue_session
ON queue_sessions ((1))
WHERE state IN ('submitting', 'reconciling', 'queued');
```

- [ ] **Step 4: Implement immutable domain models and transactional transitions**

```python
class TaskState(StrEnum):
    SCHEDULED = "scheduled"
    MONITORING = "monitoring"
    SUBMITTING = "submitting"
    RECONCILING = "reconciling"
    QUEUED = "queued"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"
    NEEDS_ATTENTION = "needs_attention"


@dataclass(frozen=True)
class TaskDraft:
    merchant_key: str
    shop_id: int
    arrival_at: datetime
    source_timezone: str
    adult_count: int
    child_count: int
    answer1: int | None
    answer2: int | None
    in_advance_information: str = ""
    early_tolerance_minutes: int = 15
    model_error_minutes: int = 15
```

Every transition updates the task version with `WHERE id=? AND version=? AND state IN (...)`, appends one event in the same transaction, and raises `ConcurrentTaskUpdate` when no row changes.

- [ ] **Step 5: Run repository tests**

Run: `uv run pytest tests/unit/automation/test_repository.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/matoca_service/automation src/matoca_service/storage/migrations.py tests/unit/automation
git commit -m "feat: persist automation task state"
```

### Task 2: Hierarchical Prediction Model

**Files:**
- Create: `src/matoca_service/prediction/__init__.py`
- Create: `src/matoca_service/prediction/models.py`
- Create: `src/matoca_service/prediction/model.py`
- Create: `src/matoca_service/prediction/repository.py`
- Test: `tests/unit/prediction/test_model.py`
- Test: `tests/unit/prediction/test_repository.py`

**Interfaces:**
- Produces: `PredictionInput`, `PredictionLevel`, `PredictionResult`, `WeightedSample`, `PredictionHierarchy`, and `PredictionModel.predict(input: PredictionInput, hierarchy: PredictionHierarchy) -> PredictionResult`.
- Produces: `PredictionRepository.samples_for(input: PredictionInput) -> PredictionHierarchy` and `save_confirmed_sample(session: QueueSession, called_at: datetime) -> PredictionSample`.
- Consumes: confirmed queue sessions from Task 1 and current fresh observations from the storage plan.

- [ ] **Step 1: Write failing cold-start and timing-bucket tests**

```python
def test_cold_start_uses_matoca_estimate_with_low_confidence() -> None:
    result = PredictionModel().predict(
        PredictionInput("sawayaka", 3272, observed_at, waiting_groups=10, matoca_minutes=25),
        PredictionHierarchy.empty(),
    )

    assert result.fast_minutes == 25
    assert result.typical_minutes == 25
    assert result.level is PredictionLevel.COLD_START
    assert result.confidence == "low"


def test_local_samples_use_weekend_three_hour_bucket() -> None:
    ratios = [0.4, 0.5, 0.6, 0.7, 0.8]
    hierarchy = hierarchy_with_ratios(merchant=ratios, shop=ratios, local=ratios)
    result = PredictionModel().predict(prediction_input(matoca_minutes=100), hierarchy)

    assert result.fast_minutes == 40
    assert result.typical_minutes == 60
    assert result.level is PredictionLevel.SHOP_DAYPART
```

- [ ] **Step 2: Write failing recency and shrinkage tests**

```python
def test_local_quantiles_are_shrunk_toward_parent() -> None:
    hierarchy = hierarchy_with_ratios(merchant=[1.0] * 20, shop=[0.5] * 10)
    result = PredictionModel().predict(prediction_input(matoca_minutes=100), hierarchy)

    assert result.typical_minutes == 75


def test_sample_weight_has_thirty_day_half_life() -> None:
    assert sample_weight(observed_at=now - timedelta(days=30), now=now) == pytest.approx(0.5)
```

- [ ] **Step 3: Run prediction tests and confirm failure**

Run: `uv run pytest tests/unit/prediction -v`

Expected: FAIL because the prediction package does not exist.

- [ ] **Step 4: Implement deterministic weighted quantiles and hierarchy**

```python
@dataclass(frozen=True)
class PredictionResult:
    fast_minutes: int
    typical_minutes: int
    confidence: Literal["low", "medium", "high"]
    level: PredictionLevel
    effective_sample_count: float


def shrink(local: float, parent: float, effective_count: float) -> float:
    weight = effective_count / (effective_count + 10.0)
    return weight * local + (1.0 - weight) * parent
```

Use ratio `actual_wait_minutes / matoca_minutes_at_submission`. Ignore samples with missing/nonpositive Matoca estimates. Weight by `0.5 ** (age_days / 30)`. Calculate weighted P20 and P50 at merchant, shop, then shop weekday/weekend plus three-hour bucket, shrinking each populated child toward its parent. Round final minutes upward and clamp at zero.

Confidence is low below 5 effective local samples, medium from 5 through less than 20, and high at 20 or more. When no level has samples, return ratio 1.0 and cold-start level.

- [ ] **Step 5: Implement repository hierarchy queries and confirmed sample creation**

Derive `weekday` versus `weekend` and the three-hour bucket using the task's source time zone. Persist the original Matoca estimate, actual wait, ratio, shop, merchant, local weekday class, local bucket, and confirmation timestamp. Do not create a sample for cancelled or unknown sessions.

- [ ] **Step 6: Run prediction tests**

Run: `uv run pytest tests/unit/prediction -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/prediction tests/unit/prediction
git commit -m "feat: add hierarchical wait prediction"
```

### Task 3: Resolve Dynamic Queue Forms and Correct Manual Defaults

**Files:**
- Modify: `src/matoca_service/matoca/models.py`
- Create: `src/matoca_service/automation/forms.py`
- Modify: `src/matoca_service/service.py`
- Modify: `src/matoca_service/web/templates/merchant.html`
- Modify: `src/matoca_service/web/static/merchant.js`
- Test: `tests/unit/automation/test_forms.py`
- Modify: `tests/unit/matoca/test_shop_models.py`
- Modify: `tests/unit/test_service.py`

**Interfaces:**
- Produces: `ConfirmSubItem`, `ConfirmItem`, and typed `ShopForms.confirm_items`.
- Produces: `ResolvedQueueForm` and `resolve_queue_form(shop: Shop, preferences: UserPreferences, requested_adult: int | None = None, requested_child: int | None = None) -> ResolvedQueueForm`.
- Consumes: `UserPreferences` from storage and existing `QueueSubmission`.

- [ ] **Step 1: Write failing Sawayaka and La Ohana form tests**

```python
def test_sawayaka_hides_child_and_auto_selects_sole_confirmation() -> None:
    resolved = resolve_queue_form(sawayaka_shop(), UserPreferences())

    assert resolved.adult_count == 2
    assert resolved.show_child is False
    assert resolved.child_count == 0
    assert resolved.answer1 == 0
    assert resolved.confirmations[0].selected_text == "同意する"


def test_la_ohana_shows_child_and_has_no_confirmation() -> None:
    resolved = resolve_queue_form(la_ohana_shop(), UserPreferences())

    assert resolved.show_child is True
    assert resolved.adult_count == 2
    assert resolved.child_count == 0
    assert resolved.confirmations == ()
```

- [ ] **Step 2: Write failing ambiguous-option test**

```python
def test_multiple_meaningful_options_block_automatic_resolution() -> None:
    with pytest.raises(UnsupportedQueueForm, match="選択内容の確認が必要です"):
        resolve_queue_form(shop_with_two_enabled_options(), UserPreferences())
```

- [ ] **Step 3: Run focused tests and confirm failure**

Run: `uv run pytest tests/unit/automation/test_forms.py tests/unit/matoca/test_shop_models.py -v`

Expected: FAIL because confirm items and resolver are not typed.

- [ ] **Step 4: Add explicit confirmation models and resolver**

```python
class ConfirmSubItem(MatocaModel):
    sub_item_index: int
    text: str
    enable: bool = True
    disabled: bool = False


class ConfirmItem(MatocaModel):
    title: str
    enable: bool = False
    sub_items: list[ConfirmSubItem] = Field(default_factory=list)


class ShopForms(MatocaModel):
    # existing fields remain
    confirm_items: list[ConfirmItem] = Field(default_factory=list)
```

The resolver clamps the global adult preference to live limits. It hides and forces child zero when `is_confirm_child` is false; otherwise it clamps the child preference. For each enabled confirmation, select its sole enabled, non-disabled option. Support at most the two answer fields proven by the captured request; raise `UnsupportedQueueForm` otherwise.

- [ ] **Step 5: Correct the existing manual dialog behavior**

Give the child row a stable element ID, hide it when `show_child` is false, initialize counts from the resolved API response rather than hard-coded JavaScript, and render the sole confirmation as already selected. Keep the user-facing copy Japanese.

- [ ] **Step 6: Run form, service, and Web tests**

Run: `uv run pytest tests/unit/automation/test_forms.py tests/unit/matoca/test_shop_models.py tests/unit/test_service.py tests/unit/web/test_app.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/matoca/models.py src/matoca_service/automation/forms.py src/matoca_service/service.py src/matoca_service/web tests/unit
git commit -m "fix: honor live merchant queue forms"
```

### Task 4: Preferences and Automation Task API

**Files:**
- Create: `src/matoca_service/automation/service.py`
- Create: `src/matoca_service/web/schemas.py`
- Modify: `src/matoca_service/web/app.py`
- Modify: `src/matoca_service/web/timezone.py`
- Test: `tests/unit/automation/test_service.py`
- Modify: `tests/unit/web/test_app.py`

**Interfaces:**
- Produces: `CreateTaskCommand` and `AutomationService.create_task(command: CreateTaskCommand, timezone: ZoneInfo) -> AutomationTask`, `list_tasks()`, `update_task()`, and `cancel_task()`.
- Produces: `GET/PUT /api/preferences` and CRUD routes under `/api/automation/tasks`.
- Consumes: `AutomationRepository`, `PreferenceRepository`, `MatocaService.shop_detail()`, and `resolve_queue_form()`.

- [ ] **Step 1: Write failing API tests for browser timezone and defaults**

```python
@pytest.mark.asyncio
async def test_create_task_converts_browser_arrival_to_utc() -> None:
    response = await client.post(
        "/api/automation/tasks",
        headers={"Origin": "http://test", "X-Timezone": "Asia/Tokyo"},
        json={"merchant_key": "sawayaka", "shop_id": 3272,
              "arrival_local": "2026-09-10T18:00:00", "adult_count": 2, "child_count": 0},
    )

    assert response.status_code == 201
    assert response.json()["arrival_at"] == "2026-09-10T09:00:00Z"
    assert response.json()["early_tolerance_minutes"] == 15
```

- [ ] **Step 2: Write failing same-origin and past-arrival tests**

```python
@pytest.mark.asyncio
async def test_task_mutations_require_same_origin() -> None:
    response = await client.post("/api/automation/tasks", headers={"Origin": "https://invalid"}, json=valid_task_json())
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_arrival_must_be_in_the_future() -> None:
    response = await same_origin_client.post("/api/automation/tasks", json=past_task_json())
    assert response.status_code == 422
    assert response.json()["detail"] == "到着予定は未来の時刻を指定してください"
```

- [ ] **Step 3: Run focused tests and confirm failure**

Run: `uv run pytest tests/unit/automation/test_service.py tests/unit/web/test_app.py -v`

Expected: FAIL because task and preference APIs do not exist.

- [ ] **Step 4: Implement request/response schemas and service**

```python
class AutomationTaskRequest(BaseModel):
    merchant_key: str
    shop_id: int = Field(ge=1)
    arrival_local: datetime
    adult_count: int = Field(ge=0)
    child_count: int = Field(ge=0)
    early_tolerance_minutes: int | None = Field(default=None, ge=0, le=30)
    model_error_minutes: int | None = Field(default=None, ge=0, le=60)
```

Map `AutomationTaskRequest` to the domain `CreateTaskCommand`; the automation package must not import Web schemas. Reject timezone-aware `arrival_local`; interpret it in the validated `X-Timezone`, defaulting to Asia/Tokyo. Fetch live shop detail, resolve/validate the form, and persist resolved answer fields. Updating a task creates a new version only while it is scheduled or monitoring. Cancellation never calls Matoca DELETE unless the task already owns a queued session and the user separately uses the existing queue-cancel action.

- [ ] **Step 5: Add routes with existing same-origin guard**

Expose list/create/get/update/cancel task routes. Use stable Japanese error messages for unknown shop, unsupported form, invalid state, and concurrent update. Never serialize internal exception strings or upstream bodies.

- [ ] **Step 6: Run API and service tests**

Run: `uv run pytest tests/unit/automation/test_service.py tests/unit/web/test_app.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/automation/service.py src/matoca_service/web tests/unit/automation/test_service.py tests/unit/web/test_app.py
git commit -m "feat: expose queue automation tasks"
```

### Task 5: Timing Decision and Safe Submission Runner

**Files:**
- Create: `src/matoca_service/automation/decision.py`
- Create: `src/matoca_service/automation/runner.py`
- Modify: `src/matoca_service/service.py`
- Test: `tests/unit/automation/test_decision.py`
- Test: `tests/unit/automation/test_runner.py`

**Interfaces:**
- Produces: `evaluate_timing(task: AutomationTask, prediction: PredictionResult, observed_at: datetime) -> TimingDecision`.
- Produces: `AutomationRunner.evaluate(task_id: int, now: datetime) -> AutomationTask` and `reconcile(task_id: int, now: datetime) -> AutomationTask`.
- Consumes: task/prediction repositories, `MatocaService.shop_detail()`, `current_waiting()`, and `create_waiting()`.

- [ ] **Step 1: Write failing timing-rule tests**

```python
def test_submit_when_error_adjusted_fast_call_reaches_early_boundary() -> None:
    task = task_arriving_at(datetime(2026, 9, 10, 10, tzinfo=UTC), early=15, error=15)
    prediction = prediction(fast_minutes=45)

    decision = evaluate_timing(task, prediction, datetime(2026, 9, 10, 9, 15, tzinfo=UTC))

    assert decision.should_submit is True
    assert decision.conservative_call_at == datetime(2026, 9, 10, 9, 45, tzinfo=UTC)


def test_wait_when_conservative_call_would_be_too_early() -> None:
    task = task_arriving_at(datetime(2026, 9, 10, 10, tzinfo=UTC), early=15, error=15)
    assert evaluate_timing(task, prediction(fast_minutes=30), datetime(2026, 9, 10, 9, tzinfo=UTC)).should_submit is False
```

- [ ] **Step 2: Write failing persist-before-POST and ambiguous-result tests**

```python
@pytest.mark.asyncio
async def test_runner_persists_submitting_before_create_call() -> None:
    upstream = RecordingQueueWriter(repository)
    await runner_for(upstream).evaluate(task.id, now)

    assert upstream.state_seen_during_create is TaskState.SUBMITTING


@pytest.mark.asyncio
async def test_timeout_reconciles_without_second_post() -> None:
    upstream = QueueWriter(create_error=httpx.ReadTimeout("timeout"), current_waiting=[])
    result = await runner_for(upstream).evaluate(task.id, now)

    assert upstream.create_calls == 1
    assert result.state is TaskState.NEEDS_ATTENTION
```

- [ ] **Step 3: Run decision and runner tests and confirm failure**

Run: `uv run pytest tests/unit/automation/test_decision.py tests/unit/automation/test_runner.py -v`

Expected: FAIL because decision and runner modules do not exist.

- [ ] **Step 4: Implement the exact timing formula**

```python
conservative_call_at = observed_at + timedelta(
    minutes=max(0, prediction.fast_minutes - task.model_error_minutes)
)
should_submit = conservative_call_at >= task.arrival_at - timedelta(
    minutes=task.early_tolerance_minutes
)
```

Return `wait`, `submit`, `expired_unavailable`, or `stale` as a stable decision code. Require a detail-fresh observation no older than two minutes. Current `is_open`, `is_issuable`, holiday, suspension, coordinates, and form validity remain mandatory.

- [ ] **Step 5: Implement persist-before-send and reconciliation**

Transition to `submitting` before calling `create_waiting`. On success create the queue session and transition to `queued` in one database transaction. On timeout, transport failure, 5xx, or process recovery, transition to `reconciling` and query current waiting once. A matching shop becomes queued; another queue or no confirmable queue becomes `needs_attention`. Never issue a second POST from reconciliation.

An explicit live validation rejection returns to `monitoring` only if it is transient and before arrival; form incompatibility and an existing queue become `needs_attention`. Current unavailability at or after arrival becomes `expired`.

- [ ] **Step 6: Run automation safety tests**

Run: `uv run pytest tests/unit/automation/test_decision.py tests/unit/automation/test_runner.py tests/unit/test_service.py -v`

Expected: PASS with exactly one mocked POST in every submission case.

- [ ] **Step 7: Commit**

```bash
git add src/matoca_service/automation src/matoca_service/service.py tests/unit/automation tests/unit/test_service.py
git commit -m "feat: submit scheduled queues safely"
```

### Task 6: Active Queue Tracking and Confirmed Training Samples

**Files:**
- Create: `src/matoca_service/automation/tracking.py`
- Modify: `src/matoca_service/automation/runner.py`
- Modify: `src/matoca_service/automation/repository.py`
- Modify: `src/matoca_service/prediction/repository.py`
- Test: `tests/unit/automation/test_tracking.py`

**Interfaces:**
- Produces: `QueueTracker.observe(task: AutomationTask, waiting: Sequence[Waiting], observed_at: datetime) -> TrackingResult`.
- Consumes: queue-session repositories and `PredictionRepository.save_confirmed_sample()`.

- [ ] **Step 1: Write failing confirmed-zero and disappeared-ticket tests**

```python
def test_first_zero_confirms_call_and_creates_training_sample() -> None:
    result = tracker.observe(queued_task(), [Waiting(id=125, count=0)], called_at)

    assert result.session.state == "completed"
    assert result.sample.actual_wait_minutes == 41


def test_disappearance_without_zero_is_unknown_and_not_training_data() -> None:
    result = tracker.observe(queued_task(), [], observed_at)

    assert result.session.state == "unknown"
    assert result.sample is None
```

- [ ] **Step 2: Write failing cancellation exclusion test**

```python
def test_service_cancelled_session_never_creates_sample() -> None:
    session = repository.mark_cancelled(session_id, cancelled_at)
    assert prediction_repository.sample_for_session(session.id) is None
```

- [ ] **Step 3: Run tracking tests and confirm failure**

Run: `uv run pytest tests/unit/automation/test_tracking.py -v`

Expected: FAIL because tracking does not exist.

- [ ] **Step 4: Implement queue observations and terminal classification**

Upsert one queue-session observation per minute. Preserve the first observed zero as `called_at`, transition task/session to completed, and create one prediction sample using the submission-time Matoca estimate. If the waiting ID disappears before zero, mark unknown. If cancellation was performed through the application, mark cancelled before the next observation so disappearance cannot be mislabeled.

Calculate the active-ticket call estimate from recent groups-ahead movement only when at least two decreasing observations exist; otherwise retain the pre-submission prediction and label it low confidence.

- [ ] **Step 5: Run tracking and prediction tests**

Run: `uv run pytest tests/unit/automation/test_tracking.py tests/unit/prediction -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/matoca_service/automation src/matoca_service/prediction tests/unit/automation tests/unit/prediction
git commit -m "feat: learn from confirmed queue journeys"
```

### Task 7: Automation Coordinator, Recovery, and Phase Verification

**Files:**
- Create: `src/matoca_service/automation/coordinator.py`
- Modify: `src/matoca_service/collection/coordinator.py`
- Modify: `src/matoca_service/web/app.py`
- Modify: `README.md`
- Test: `tests/unit/automation/test_coordinator.py`
- Modify: `tests/unit/web/test_lifespan.py`

**Interfaces:**
- Produces: `AutomationCoordinator.start() -> None`, `run_once(now: datetime | None = None) -> None`, and `stop() -> None`.
- Adds: collection schedule callback `has_active_task(merchant_key: str) -> bool`.
- Consumes: `AutomationRunner`, `QueueTracker`, `AutomationRepository`, and the FastAPI lifespan established in the storage plan.

- [ ] **Step 1: Write failing startup recovery test**

```python
@pytest.mark.asyncio
async def test_startup_reconciles_submitting_before_evaluating_monitoring_tasks() -> None:
    repository = repository_with_tasks(states=[TaskState.SUBMITTING, TaskState.MONITORING])
    coordinator = coordinator_for(repository)

    await coordinator.run_once(now)

    assert coordinator.runner.calls[0].operation == "reconcile"
    assert coordinator.runner.calls[1].operation == "evaluate"
```

- [ ] **Step 2: Write failing active-task polling override test**

```python
def test_monitoring_task_forces_one_minute_collection() -> None:
    assert schedule.next_interval("sawayaka", outside_window, has_active_task=True) == timedelta(minutes=1)
```

- [ ] **Step 3: Run coordinator tests and confirm failure**

Run: `uv run pytest tests/unit/automation/test_coordinator.py tests/unit/collection/test_schedule.py tests/unit/web/test_lifespan.py -v`

Expected: FAIL because automation lifecycle is not wired.

- [ ] **Step 4: Implement one coordinator task and startup ordering**

On startup initialize the database, recover `submitting` and `reconciling` tasks first, then start collection and automation loops. Evaluate scheduled/monitoring tasks from the newest complete observation at most once per observed minute. Track queued sessions each minute. Sleep using an `asyncio.Event` so shutdown is immediate.

- [ ] **Step 5: Wire lifecycle and update README**

Start both coordinators only for the real application assembly. Stop automation before collection on shutdown so no decision reads a partially closing collector. Document task authorization, timing defaults, cold start, no closing prediction, restart reconciliation, and the prohibition on unconfirmed live destructive tests.

- [ ] **Step 6: Run the full quality gate**

Run: `uv run pytest`

Expected: PASS with live/destructive tests skipped.

Run: `uv run ruff check src tests`

Expected: PASS.

Run: `uv run ruff format --check src tests`

Expected: PASS.

Run: `uv run mypy src`

Expected: PASS.

Run: `uv build`

Expected: package artifacts build without runtime state.

- [ ] **Step 7: Commit**

```bash
git add README.md src/matoca_service/automation src/matoca_service/collection src/matoca_service/web/app.py tests
git commit -m "feat: run restart-safe queue automation"
```
