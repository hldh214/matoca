import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

import matoca_service.service as service_module
from matoca_service.automation.models import AutomationRequest
from matoca_service.automation.repository import TaskConflictError
from matoca_service.automation.runner import AutomationRunner
from matoca_service.matoca.models import Shop, ShopForms, Waiting, WaitingEstimate
from matoca_service.service import MatocaService, QueueSubmission, QueueUnavailableError
from matoca_service.storage.models import CollectionWrite, ShopObservation


class FakeClient:
    def __init__(self) -> None:
        self.shop = Shop(
            id=3272,
            name="テスト店",
            lat=34,
            lng=137,
            is_open=True,
            is_issuable=True,
            forms=ShopForms(min_adult=1, max_adult=8),
            waiting_time=WaitingEstimate(minutes=30),
        )
        self.waiting: list[Waiting] = []
        self.posts = 0
        self.on_send = None
        self.error = False

    async def authenticate(self):
        return None

    async def get_shop(self, *args, **kwargs):
        return self.shop

    async def list_waiting(self):
        return self.waiting

    async def create_waiting(self, request):
        self.posts += 1
        if self.on_send:
            await self.on_send()
        self.waiting = [Waiting(id=42, shop_id=3272, count=8, adult_count=2, child_count=0)]
        if self.error:
            raise httpx.ReadTimeout("synthetic")
        return self.waiting[0]


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    config = tmp_path / "line.toml"
    config.write_text(
        'host="example.invalid"\napplication="test"\nlocale="ja_JP"\nprotocol_version="1"\nuser_agent="test"\n'
    )
    service = MatocaService(config, tmp_path / "state.json", tmp_path / "data/db.sqlite")
    # Legacy replay fixtures explicitly retain history; the running service does not.
    from matoca_service.storage.repositories import ShopRepository

    service._shops = ShopRepository(service._database)
    client = FakeClient()
    other = FakeClient()
    now = [datetime.now(UTC)]
    service._shops.save_cycle(
        CollectionWrite(
            merchant_key="sawayaka",
            observed_at=now[0],
            shops=[ShopObservation(shop=client.shop, list_fresh=True, detail_fresh=True)],
        )
    )

    async def native(*args, **kwargs):
        return None

    async def liff(*args, **kwargs):
        return SimpleNamespace(access_token="synthetic")

    async def read(merchant, operation):
        return await operation(client if merchant == "sawayaka" else other)

    monkeypatch.setattr(service_module.TokenManager, "ensure_native_token", native)
    monkeypatch.setattr(service_module.TokenManager, "ensure_liff_token", liff)
    monkeypatch.setattr(service_module, "MatocaClient", lambda *args: client)
    monkeypatch.setattr(service, "_authenticated_read", read)
    runner = AutomationRunner(service, service._automation, now=lambda: now[0])
    service._automation_runner = runner

    # Existing workflow tests have an established pre-call model. Dedicated
    # cold-start tests below exercise the no-evidence behavior separately.
    def seed_pre_call(c):
        end = now[0] - timedelta(days=1)
        start = end - timedelta(minutes=30)
        cursor = c.execute(
            """INSERT INTO queue_sessions
            (merchant_key, shop_id, waiting_id, source, submitted_at, first_observed_at,
             official_minutes_at_submission, official_is_more_at_submission, status)
            VALUES ('sawayaka',3272,999999,'manual',?,?,30,0,'cancelled')""",
            (start.isoformat(), start.isoformat()),
        )
        c.execute(
            "INSERT INTO queue_milestones VALUES (?, 'pre_call', ?, ?, 'api_observation', 60)",
            (cursor.lastrowid, end.isoformat(), end.isoformat()),
        )

    service._database.write(seed_pre_call)
    return service, runner, client, other, now


def request(arrival):
    return AutomationRequest(
        merchant_key="sawayaka",
        shop_id=3272,
        arrival_at=arrival,
        timezone="Asia/Tokyo",
        consent=True,
    )


@pytest.mark.asyncio
async def test_no_pre_call_samples_waits_until_arrival_even_with_early_tolerance(setup):
    service, runner, client, _, now = setup
    service._database.write(lambda c: c.execute("DELETE FROM queue_milestones"))
    arrival = now[0] + timedelta(minutes=10)
    task = await runner.create(request(arrival).model_copy(update={"early_tolerance_minutes": 120}))
    await runner.run_once()
    assert service._automation.get(task.id).state == "monitoring"
    assert service._automation.list_decisions(task.id)[-1].reason_code == "pre_call_samples_missing"
    assert client.posts == 0
    now[0] = arrival
    await runner.run_once()
    assert service._automation.get(task.id).state == "queued"
    assert client.posts == 1


@pytest.mark.asyncio
async def test_zero_groups_keeps_task_queued_until_official_call(setup):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=30)))
    await runner.run_once()
    assert service._automation.get(task.id).state == "queued"
    client.waiting[0] = client.waiting[0].model_copy(update={"count": 0, "status": 8})
    now[0] += timedelta(minutes=1)
    await runner.run_once()
    assert service._automation.get(task.id).state == "queued"
    assert service._queues.list_sessions()[0].called_at is None
    client.waiting[0] = client.waiting[0].model_copy(update={"status": 4})
    now[0] += timedelta(minutes=1)
    await runner.run_once()
    assert service._automation.get(task.id).state == "completed"
    assert service._queues.list_sessions()[0].called_at == now[0]
    assert client.posts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "simulation"])
@pytest.mark.parametrize("failure", ["closed", "read"])
async def test_unsent_failure_retries_through_arrival_grace(setup, monkeypatch, mode, failure):
    service, runner, client, _, now = setup
    arrival = now[0] + timedelta(minutes=1)
    task = await runner.create(request(arrival).model_copy(update={"mode": mode}))
    original = client.get_shop

    async def failing_read(*args, **kwargs):
        raise httpx.ReadTimeout("synthetic")

    if failure == "closed":
        client.shop.is_issuable = False
    else:
        monkeypatch.setattr(client, "get_shop", failing_read)
    now[0] = arrival + timedelta(seconds=30)
    await runner.run_once()
    current = service._automation.get(task.id)
    assert current.state == "monitoring"
    assert current.intent_id is None
    assert client.posts == 0
    client.shop.is_issuable = True
    monkeypatch.setattr(client, "get_shop", original)
    now[0] = arrival + timedelta(minutes=2)
    await runner.run_once()
    assert service._automation.get(task.id).state == ("queued" if mode == "live" else "simulated")
    assert client.posts == (1 if mode == "live" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "simulation"])
async def test_unavailable_grace_expires_without_submission(setup, mode):
    service, runner, client, _, now = setup
    arrival = now[0] + timedelta(minutes=1)
    task = await runner.create(request(arrival).model_copy(update={"mode": mode}))
    client.shop.is_open = False
    now[0] = arrival + timedelta(minutes=2)
    await runner.run_once()
    assert service._automation.get(task.id).state == "monitoring"
    now[0] += timedelta(microseconds=1)
    await runner.run_once()
    assert service._automation.get(task.id).state == "expired"
    assert client.posts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flags", "code", "message"),
    [
        ({"is_holiday": True}, "shop_holiday", "本日は休業です"),
        ({"is_suspended": True}, "shop_suspended", "受付を一時停止しています"),
        ({"is_open": False}, "shop_closed", "現在は営業時間外です"),
        ({"is_issuable": False}, "reception_closed", "現在は順番待ちを受け付けていません"),
    ],
)
async def test_unavailable_task_records_specific_shop_reason(setup, flags, code, message):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=60)))
    client.shop = client.shop.model_copy(update=flags)
    await runner.run_once()
    current = service._automation.get(task.id)
    assert current.state == "monitoring"
    assert message in current.last_decision
    assert service._automation.list_decisions(task.id)[-1].reason_code == code
    assert client.posts == 0


def edit_body(task, **changes):
    import json

    from matoca_service.automation.runner import form_revision

    return {
        **task.model_dump(
            mode="json",
            include={
                "arrival_at",
                "timezone",
                "adult_count",
                "child_count",
                "answer1",
                "answer2",
                "in_advance_information",
                "early_tolerance_minutes",
                "model_error_minutes",
                "consent",
            },
        ),
        "expected_version": task.version,
        "form_revision": form_revision(
            Shop(
                id=task.shop_id,
                name=task.shop_name,
                forms=ShopForms.model_validate(json.loads(task.form_signature)),
            )
        ),
        **changes,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,intent",
    [("cancelled", None), ("submitting", None), ("needs_attention", "resolved-intent")],
)
async def test_edit_repository_cas_checks_state_and_intent_even_without_version_bump(
    setup, state, intent
):
    service, runner, _, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=60)))
    if intent:
        from matoca_service.tracking.models import QueueIntent

        service._queues.begin_intent(
            QueueIntent(
                intent_id=intent,
                merchant_key="sawayaka",
                shop_id=3272,
                submitted_at=now[0],
                adult_count=2,
                child_count=0,
                source="automation",
                official_minutes_at_submission=30,
            )
        )
    service._database.write(
        lambda c: c.execute(
            "UPDATE automation_tasks SET state=?, intent_id=? WHERE id=?", (state, intent, task.id)
        )
    )
    with pytest.raises(TaskConflictError):
        service._automation.edit(task, request(task.arrival_at), task.form_signature, now[0])
    assert service._automation.get(task.id).state == state


@pytest.mark.asyncio
@pytest.mark.parametrize("estimate", [None, WaitingEstimate(minutes=60, is_more=True)])
async def test_arrival_allows_missing_or_lower_bound_estimate_with_fresh_live_checks(
    setup, estimate
):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=60)))
    client.shop.waiting_time = estimate
    await runner.run_once()
    assert service._automation.get(task.id).state == "monitoring"
    now[0] += timedelta(minutes=60)
    await runner.run_once()
    assert service._automation.get(task.id).state == "queued"
    assert client.posts == 1


@pytest.mark.asyncio
async def test_form_defaults_metadata_and_still_valid_bounds_do_not_block(setup):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    client.shop.forms = ShopForms(min_adult=1, max_adult=6, default_value_adult=4, decoration="new")
    await runner.run_once()
    assert service._automation.get(task.id).state == "queued"


@pytest.mark.asyncio
async def test_simulation_account_conflict_is_audited_without_queue_or_push_writes(setup):
    service, runner, _, other, now = setup
    payload = request(now[0] + timedelta(minutes=10)).model_dump()
    payload["mode"] = "simulation"
    task = await runner.create(AutomationRequest.model_validate(payload))
    other.waiting = [Waiting(id=99)]
    await runner.run_once()
    assert service._automation.get(task.id).state == "needs_attention"
    assert service._automation.list_decisions(task.id)[0].would_submit is False
    assert [s.waiting_id for s in service._queues.list_sessions()] == [999999]
    assert (
        service._database.read(
            lambda c: c.execute("SELECT count(*) FROM notification_outbox").fetchone()[0]
        )
        == 0
    )


@pytest.mark.asyncio
async def test_simulation_refuses_unresolved_account_intent(setup):
    service, runner, client, _, now = setup
    from matoca_service.tracking.models import QueueIntent

    intent = QueueIntent(
        intent_id="uncertain",
        merchant_key="sawayaka",
        shop_id=3272,
        submitted_at=now[0],
        adult_count=2,
        child_count=0,
        source="manual",
        official_minutes_at_submission=None,
    )
    service._queues.begin_intent(intent)
    payload = request(now[0] + timedelta(minutes=10)).model_dump()
    payload["mode"] = "simulation"
    task = await runner.create(AutomationRequest.model_validate(payload))
    await runner.run_once()
    assert service._automation.get(task.id).state == "needs_attention"
    assert service._automation.list_decisions(task.id)[0].would_submit is False
    assert client.posts == 0


def test_task_mode_cannot_be_assigned_into_live():
    payload = request(datetime(2030, 1, 1, tzinfo=UTC)).model_dump()
    payload["mode"] = "simulation"
    simulation = AutomationRequest.model_validate(payload)
    with pytest.raises(ValidationError):
        simulation.mode = "live"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,expected",
    [
        ("unselected", "queued"),
        ("selected", "needs_attention"),
        ("title", "needs_attention"),
        ("required", "needs_attention"),
        ("item_required", "needs_attention"),
        ("option_required", "needs_attention"),
        ("item_enabled_input", "needs_attention"),
        ("decoration", "queued"),
    ],
)
async def test_form_signature_preserves_selected_meaning_and_required_fields(
    setup, change, expected
):
    service, runner, client, _, now = setup
    items = [
        {
            "enable": True,
            "title": "座席",
            "sub_items": [
                {"enable": True, "sub_item_index": 0, "text": "テーブル"},
                {"enable": True, "sub_item_index": 1, "text": "カウンター"},
            ],
        }
    ]
    client.shop.forms = ShopForms(min_adult=1, max_adult=8, confirm_items=items)
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    if change == "title":
        items[0]["title"] = "別の質問"
    elif change in {"item_required", "option_required", "item_enabled_input"}:
        target = items[0]["sub_items"][0] if change == "option_required" else items[0]
        target["extra_inputs"] = [
            {"enable" if change == "item_enabled_input" else "required": True, "name": "電話"}
        ]
    elif change == "decoration":
        client.shop.forms = ShopForms(
            min_adult=1,
            max_adult=8,
            confirm_items=items,
            decoration={"enable": True, "color": "red"},
        )
        items[0]["decoration"] = {"enable": True, "color": "blue"}
        items[0]["sub_items"][0]["decoration"] = {"enable": True, "color": "blue"}
    elif change == "required":
        client.shop.forms = ShopForms(
            min_adult=1,
            max_adult=8,
            confirm_items=items,
            extra_inputs=[{"required": True, "name": "電話"}],
        )
    else:
        items[0]["sub_items"][0 if change == "selected" else 1]["text"] = "新しい選択肢"
    await runner.run_once()
    assert service._automation.get(task.id).state == expected
    assert client.posts == int(expected == "queued")


def test_legacy_task_payload_defaults_to_live(setup):
    import json

    from matoca_service.automation.runner import form_signature

    service, _, client, _, now = setup
    task = service._automation.create(
        request(now[0] + timedelta(minutes=10)), "店", form_signature(client.shop), now[0]
    )

    def legacy(connection):
        payload = json.loads(
            connection.execute(
                "SELECT payload FROM automation_tasks WHERE id=?", (task.id,)
            ).fetchone()[0]
        )
        del payload["mode"]
        connection.execute(
            "UPDATE automation_tasks SET payload=? WHERE id=?", (json.dumps(payload), task.id)
        )

    service._database.write(legacy)
    assert service._automation.get(task.id).mode == "live"


def test_replay_uses_each_observation_time_for_labels_and_bounds(setup):
    from matoca_service.automation.replay import ReplayRequest, replay

    service, _, client, _, _ = setup
    start = datetime(2020, 1, 10, 3, tzinfo=UTC)
    for minute in [0, 1, 2, 3]:
        service._shops.save_cycle(
            CollectionWrite(
                merchant_key="sawayaka",
                observed_at=start + timedelta(minutes=minute),
                shops=[ShopObservation(shop=client.shop, list_fresh=True, detail_fresh=True)],
            )
        )
    submitted = start - timedelta(minutes=10)
    called = start + timedelta(minutes=2)

    def seed(connection):
        cursor = connection.execute(
            """INSERT INTO queue_sessions (merchant_key, shop_id, waiting_id, source,
            submitted_at, first_observed_at, official_minutes_at_submission,
            official_is_more_at_submission, called_at, status)
            VALUES ('sawayaka', 3272, 101, 'manual', ?, ?, 24, 0, ?, 'called')""",
            (submitted.isoformat(), submitted.isoformat(), called.isoformat()),
        )
        connection.execute(
            """INSERT INTO queue_session_observations
               (session_id, observed_minute, count) VALUES (?, ?, 0)""",
            (cursor.lastrowid, called.isoformat()),
        )

    service._database.write(seed)
    service._database.write(
        lambda c: c.execute(
            """INSERT INTO queue_milestones
           SELECT session_id,'pre_call',?,?,'api_observation',60
           FROM queue_sessions WHERE waiting_id=101""",
            (called.isoformat(), called.isoformat()),
        )
    )
    query = ReplayRequest(
        merchant_key="sawayaka",
        shop_id=3272,
        day=start.date(),
        arrival_at=start + timedelta(minutes=30),
    )
    result = replay(service._database, query, as_of=start + timedelta(minutes=2))
    assert len(result.decisions) == 3
    assert result.decisions[0].prediction.fast_minutes == 0
    assert result.decisions[1].prediction.fast_minutes == 0
    assert result.decisions[2].prediction.fast_minutes == 15
    assert result.first_would_submit_at is None


def test_replay_excludes_next_japan_day_even_with_arrival_grace(setup):
    from matoca_service.automation.replay import ReplayRequest, replay

    service, _, client, _, now = setup
    midnight = now[0].replace(hour=15, minute=0, second=0, microsecond=0)
    service._shops.save_cycle(
        CollectionWrite(
            merchant_key="sawayaka",
            observed_at=midnight,
            shops=[ShopObservation(shop=client.shop, list_fresh=True, detail_fresh=True)],
        )
    )
    query = ReplayRequest(
        merchant_key="sawayaka",
        shop_id=3272,
        day=midnight.date(),
        arrival_at=midnight - timedelta(minutes=1),
    )
    result = replay(service._database, query, as_of=midnight + timedelta(minutes=1))
    assert all(item.evaluated_at < midnight for item in result.decisions)


@pytest.mark.asyncio
async def test_timing_boundary_and_atomic_intent_before_post(setup):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=31)))
    await runner.run_once()
    assert client.posts == 0
    assert service._automation.get(task.id).state == "monitoring"
    now[0] += timedelta(minutes=1)

    async def before_send():
        persisted = service._automation.get(task.id)
        assert persisted.state == "submitting"
        assert service._queues.list_unfinished_intents()[0].intent_id == persisted.intent_id

    client.on_send = before_send
    await runner.run_once()
    assert client.posts == 1
    assert service._automation.get(task.id).state == "queued"
    assert service._queues.list_sessions()[0].source == "automation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "late,available,expected", [(1, True, "queued"), (1, False, "monitoring"), (3, True, "expired")]
)
async def test_deadline_grace_prevents_late_replay(setup, late, available, expected):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=60)))
    now[0] += timedelta(minutes=60 + late)
    client.shop.is_issuable = available
    await runner.run_once()
    assert service._automation.get(task.id).state == expected
    assert client.posts == int(expected == "queued")


@pytest.mark.asyncio
async def test_changed_live_form_requires_attention_without_post(setup):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    client.shop.forms = ShopForms(min_adult=3, max_adult=8)
    await runner.run_once()
    assert client.posts == 0
    assert service._automation.get(task.id).state == "needs_attention"


@pytest.mark.asyncio
async def test_other_merchant_queue_blocks_automation_and_manual(setup):
    service, runner, client, other, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    other.waiting = [Waiting(id=99)]
    await runner.run_once()
    assert service._automation.get(task.id).state == "needs_attention"
    with pytest.raises(QueueUnavailableError):
        await service.create_waiting(
            "sawayaka", QueueSubmission(shop_id=3272, adult_count=2, child_count=0)
        )
    assert client.posts == 0


@pytest.mark.asyncio
async def test_ambiguous_send_and_restart_reconcile_without_replay(setup):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    client.error = True
    await runner.run_once()
    assert service._automation.get(task.id).state in {"reconciling", "needs_attention"}
    now[0] += timedelta(seconds=1)
    restarted = AutomationRunner(service, service._automation, now=lambda: now[0])
    await restarted.run_once()
    assert client.posts == 1
    assert service._automation.get(task.id).state == "queued"


@pytest.mark.asyncio
async def test_monitor_cancel_wins_before_evaluation_and_never_cancels_ticket(setup):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    await runner.cancel(task.id)
    await runner.run_once()
    assert client.posts == 0
    assert service._automation.get(task.id).state == "cancelled"


@pytest.mark.parametrize(
    "changes", [{"consent": False}, {"arrival_at": datetime(2030, 1, 1)}, {"timezone": "invalid"}]
)
def test_invalid_activation_is_rejected(changes):
    with pytest.raises(ValidationError):
        AutomationRequest.model_validate(
            {
                "merchant_key": "sawayaka",
                "shop_id": 3272,
                "arrival_at": datetime(2030, 1, 1, tzinfo=UTC),
                "timezone": "Asia/Tokyo",
                "consent": True,
                **changes,
            }
        )


@pytest.mark.asyncio
async def test_cancellation_during_send_cannot_report_success(setup):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    sending, release = asyncio.Event(), asyncio.Event()

    async def block_send():
        sending.set()
        await release.wait()

    client.on_send = block_send
    running = asyncio.create_task(runner.run_once())
    await sending.wait()
    cancel = asyncio.create_task(runner.cancel(task.id))
    await asyncio.sleep(0)
    assert not cancel.done()
    release.set()
    await running
    with pytest.raises(TaskConflictError):
        await cancel
    assert service._automation.get(task.id).state == "queued"


@pytest.mark.asyncio
async def test_manual_submission_racing_automation_uses_one_account_slot(setup):
    service, runner, client, _, now = setup
    await runner.create(request(now[0] + timedelta(minutes=10)))
    sending, release = asyncio.Event(), asyncio.Event()

    async def block_send():
        sending.set()
        await release.wait()

    client.on_send = block_send
    running = asyncio.create_task(runner.run_once())
    await sending.wait()
    manual = asyncio.create_task(
        service.create_waiting(
            "sawayaka", QueueSubmission(shop_id=3272, adult_count=2, child_count=0)
        )
    )
    release.set()
    await running
    with pytest.raises(QueueUnavailableError):
        await manual
    assert client.posts == 1


@pytest.mark.asyncio
async def test_shutdown_after_durable_send_start_never_replays(setup):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    sending = asyncio.Event()

    async def interrupted_send():
        client.waiting = [Waiting(id=42, shop_id=3272, adult_count=2, child_count=0, count=8)]
        sending.set()
        await asyncio.Event().wait()

    client.on_send = interrupted_send
    running = asyncio.create_task(runner.run_once())
    await sending.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert service._automation.get(task.id).state == "submitting"
    now[0] += timedelta(seconds=1)
    await AutomationRunner(service, service._automation, now=lambda: now[0]).run_once()
    assert service._automation.get(task.id).state == "queued"
    assert client.posts == 1


@pytest.mark.asyncio
async def test_explicit_resolution_requires_successful_account_reads_and_releases_intent(setup):
    service, runner, client, other, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    client.error = True
    await runner.run_once()
    client.waiting = []
    await runner.run_once()
    assert service._automation.get(task.id).state == "needs_attention"
    other.waiting = [Waiting(id=99)]
    with pytest.raises(QueueUnavailableError):
        await runner.resolve(task.id)
    other.waiting = []
    resolved = await runner.resolve(task.id)
    assert resolved.state == "cancelled"
    assert service._queues.list_unfinished_intents() == []
    await runner.run_once()
    assert client.posts == 1


@pytest.mark.asyncio
async def test_failed_read_of_other_merchant_never_becomes_empty(setup, monkeypatch):
    service, runner, client, other, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))

    async def unavailable():
        raise httpx.ReadTimeout("synthetic")

    monkeypatch.setattr(other, "list_waiting", unavailable)
    await runner.run_once()
    assert client.posts == 0
    assert service._automation.get(task.id).state == "monitoring"
    assert service._automation.list_decisions(task.id)[0].fresh is False


@pytest.mark.asyncio
@pytest.mark.parametrize("estimate", [None, WaitingEstimate(minutes=30, is_more=True)])
async def test_missing_or_lower_bound_live_estimate_never_submits(setup, estimate):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    client.shop.waiting_time = estimate
    await runner.run_once()
    assert client.posts == 0
    assert service._automation.get(task.id).state == "monitoring"


@pytest.mark.asyncio
async def test_post_response_persistence_failure_recovers_linkage_without_replay(
    setup, monkeypatch
):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))

    def failed_save(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic")

    monkeypatch.setattr(service._queues, "resolve_intent", failed_save)
    await runner.run_once()
    assert service._automation.get(task.id).state == "reconciling"
    now[0] += timedelta(seconds=1)
    await runner.run_once()
    assert service._automation.get(task.id).state == "queued"
    assert client.posts == 1


@pytest.mark.asyncio
async def test_prediction_delay_crossing_deadline_does_not_send(setup, monkeypatch):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    original = service.predict_pre_call

    async def delayed(*args):
        result = await original(*args)
        now[0] += timedelta(hours=1)
        return result

    monkeypatch.setattr(service, "predict_pre_call", delayed)
    await runner.run_once()
    assert service._automation.get(task.id).state == "expired"
    assert client.posts == 0


@pytest.mark.asyncio
async def test_persistence_delay_cannot_authorize_hours_late_post(setup, monkeypatch):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    original = service._automation.begin_submission

    def slow_persist(*args):
        original(*args)
        now[0] += timedelta(hours=1)

    monkeypatch.setattr(service._automation, "begin_submission", slow_persist)
    await runner.run_once()
    assert service._automation.get(task.id).state == "expired"
    assert service._queues.list_unfinished_intents() == []
    assert client.posts == 0


@pytest.mark.asyncio
async def test_slow_prediction_does_not_send_using_stale_live_checks(setup, monkeypatch):
    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    original = service.predict_pre_call

    async def delayed(*args):
        result = await original(*args)
        now[0] += timedelta(minutes=2)
        return result

    monkeypatch.setattr(service, "predict_pre_call", delayed)
    await runner.run_once()
    assert service._automation.get(task.id).state == "monitoring"
    assert client.posts == 0


@pytest.mark.asyncio
async def test_manual_unknown_intent_resolution_is_explicit_and_checks_account(setup):
    from matoca_service.service import QueueOutcomeUnknownError
    from matoca_service.web.app import create_app

    service, _, client, other, _ = setup
    client.error = True
    with pytest.raises(QueueOutcomeUnknownError):
        await service.create_waiting(
            "sawayaka", QueueSubmission(shop_id=3272, adult_count=2, child_count=0)
        )
    intent = service._queues.list_unfinished_intents()[0]
    url = f"/api/queues/intents/{intent.intent_id}/resolve"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as api:
        assert (await api.post(url, json={"confirm_no_queue": True})).status_code == 403
        assert (await api.post(url, json={}, headers={"origin": "http://test"})).status_code == 422
        client.waiting = []
        other.waiting = [Waiting(id=99)]
        assert (
            await api.post(url, json={"confirm_no_queue": True}, headers={"origin": "http://test"})
        ).status_code == 409
        assert len(service._queues.list_unfinished_intents()) == 1
        other.waiting = []
        assert (
            await api.post(url, json={"confirm_no_queue": True}, headers={"origin": "http://test"})
        ).status_code == 204
    assert service._queues.list_unfinished_intents() == []
    assert client.posts == 1


@pytest.mark.asyncio
async def test_retired_automation_intent_can_be_resolved_without_resubmission(setup):
    from matoca_service.web.app import create_app

    service, runner, client, other, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    client.error = True
    await runner.run_once()
    client.waiting = []
    intent_id = service._automation.get(task.id).intent_id
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as api:
        other.waiting = [Waiting(id=99)]
        blocked = await api.post(
            f"/api/queues/intents/{intent_id}/resolve",
            json={"confirm_no_queue": True},
            headers={"origin": "http://test"},
        )
        assert blocked.status_code == 409
        assert len(service._queues.list_unfinished_intents()) == 1
        other.waiting = []
        response = await api.post(
            f"/api/queues/intents/{intent_id}/resolve",
            json={"confirm_no_queue": True},
            headers={"origin": "http://test"},
        )
        assert response.status_code == 204
    assert service._queues.list_unfinished_intents() == []
    assert service._automation.get(task.id).state == "cancelled"
    assert client.posts == 1
