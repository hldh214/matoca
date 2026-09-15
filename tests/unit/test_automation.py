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
    "late,available,expected", [(1, True, "queued"), (1, False, "expired"), (3, True, "expired")]
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
async def test_automation_api_validates_origin_consent_and_cancellation(setup):
    from matoca_service.web.app import create_app

    service, _, client, _, now = setup
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as api:
        payload = request(now[0] + timedelta(hours=1)).model_dump(mode="json")
        assert (await api.post("/api/automation/tasks", json=payload)).status_code == 403
        payload["consent"] = False
        invalid = await api.post(
            "/api/automation/tasks", json=payload, headers={"origin": "http://test"}
        )
        assert invalid.status_code == 422
        payload["consent"] = True
        response = await api.post(
            "/api/automation/tasks", json=payload, headers={"origin": "http://test"}
        )
        assert response.status_code == 201
        task = response.json()
        assert "form_signature" not in task
        assert (await api.get("/api/automation/tasks")).json()[0]["id"] == task["id"]
        cancelled = await api.delete(
            f"/api/automation/tasks/{task['id']}", headers={"origin": "http://test"}
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert client.posts == 0


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
    original = service.predict

    async def delayed(*args):
        result = await original(*args)
        now[0] += timedelta(hours=1)
        return result

    monkeypatch.setattr(service, "predict", delayed)
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
    original = service.predict

    async def delayed(*args):
        result = await original(*args)
        now[0] += timedelta(minutes=2)
        return result

    monkeypatch.setattr(service, "predict", delayed)
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
async def test_task_intent_cannot_bypass_task_resolution_via_manual_route(setup):
    from matoca_service.web.app import create_app

    service, runner, client, _, now = setup
    task = await runner.create(request(now[0] + timedelta(minutes=10)))
    client.error = True
    await runner.run_once()
    client.waiting = []
    intent_id = service._automation.get(task.id).intent_id
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as api:
        response = await api.post(
            f"/api/queues/intents/{intent_id}/resolve",
            json={"confirm_no_queue": True},
            headers={"origin": "http://test"},
        )
        assert response.status_code == 409
    assert len(service._queues.list_unfinished_intents()) == 1
