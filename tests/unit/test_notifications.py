import base64
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from matoca_service.notifications.models import PushSubscription
from matoca_service.notifications.repository import NotificationRepository
from matoca_service.storage.database import Database
from matoca_service.tracking.models import QueueIntent, QueueRead
from matoca_service.tracking.repository import QueueRepository

NOW = datetime(2026, 9, 15, 3, tzinfo=UTC)


def subscription(suffix: str = "one") -> PushSubscription:
    # A valid public P-256 point (the standard generator).
    point = bytes.fromhex(
        "046b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296"
        "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5"
    )
    return PushSubscription(
        endpoint=f"https://fcm.googleapis.com/fcm/send/{suffix}",
        keys={
            "p256dh": base64.urlsafe_b64encode(point).decode().rstrip("="),
            "auth": base64.urlsafe_b64encode(b"a" * 16).decode().rstrip("="),
        },
    )


def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "data" / "test.sqlite3")
    db.initialize()
    return db


def test_vendor_validation_and_repr_redact_subscription() -> None:
    sub = subscription()
    assert "fcm/send" not in repr(sub)
    assert sub.keys.auth not in repr(sub)
    for endpoint in [
        "http://fcm.googleapis.com/a",
        "https://127.0.0.1/a",
        "https://fcm.googleapis.com.attacker.test/a",
        "https://user@fcm.googleapis.com/a",
        "https://fcm.googleapis.com:444/a",
    ]:
        with pytest.raises(ValueError):
            PushSubscription(endpoint=endpoint, keys=sub.keys)


def test_targeted_test_and_history_never_expose_capabilities(tmp_path: Path) -> None:
    repo = NotificationRepository(database(tmp_path))
    one = repo.subscribe(subscription(), NOW)
    repo.subscribe(subscription("two"), NOW)
    repo.test(one, NOW)
    pending = repo.pending(NOW)
    assert len(pending) == 1
    assert pending[0].subscription_id == one
    history = str(repo.history())
    assert "通知のテスト" in history
    assert "fcm.googleapis.com" not in history
    assert subscription().keys.auth not in history


def test_initial_zero_and_restart_publish_deduplicated_transactional_events(tmp_path: Path) -> None:
    db = database(tmp_path)
    repo = NotificationRepository(db)
    repo.subscribe(subscription(), NOW)
    queues = QueueRepository(db)
    queues.begin_intent(
        QueueIntent(
            intent_id="i",
            merchant_key="sawayaka",
            shop_id=1,
            submitted_at=NOW,
            adult_count=2,
            child_count=0,
            source="automation",
            official_minutes_at_submission=10,
        )
    )
    queues.resolve_intent("i", waiting_id=1, number=3, count=0, observed_at=NOW)
    queues = QueueRepository(db)
    queues.record_read(
        QueueRead(
            merchant_key="sawayaka", waiting_id=1, count=0, observed_at=NOW + timedelta(minutes=1)
        )
    )
    kinds = [item.kind for item in repo.history()]
    assert kinds.count("automated_submission") == 1
    assert kinds.count("groups_0") == 1
    assert len(repo.pending(NOW)) == len(kinds)


def test_business_rollback_also_rolls_back_outbox(tmp_path: Path) -> None:
    db = database(tmp_path)
    repo = NotificationRepository(db)

    def failed(connection: sqlite3.Connection) -> None:
        repo.publish(connection, "event", "test", "通知のテスト", "確認", "/", NOW)
        raise RuntimeError("rollback")

    with pytest.raises(RuntimeError):
        db.write(failed)
    assert repo.history() == []


def test_earlier_alert_uses_absolute_trajectory_not_elapsed_time(tmp_path: Path) -> None:
    db = database(tmp_path)
    queues = QueueRepository(db)
    repo = NotificationRepository(db)
    for minute, count in [(0, 40), (5, 35), (10, 30), (15, 5)]:
        queues.record_read(
            QueueRead(
                merchant_key="sawayaka",
                waiting_id=1,
                shop_id=1,
                observed_at=NOW + timedelta(minutes=minute),
                count=count,
            )
        )
        early = [x for x in repo.history() if x.kind == "predicted_earlier"]
        assert len(early) == (1 if minute == 15 else 0)
    assert "待ち組数の減少" in early[0].body


def test_vapid_generation_preserves_latest_line_state_and_is_stable(tmp_path: Path) -> None:
    from matoca_service.notifications.keys import VapidKeys
    from matoca_service.state.models import AppState, LineState
    from matoca_service.state.store import JsonStateStore

    store = JsonStateStore(tmp_path / "state.json")
    store.save(
        AppState(
            line=LineState(access_token="new-access", refresh_token="new-refresh", adid="device")
        )
    )
    keys = VapidKeys(store)
    assert keys.public_key() is None
    first = keys.enable("https://queue.example.test")
    assert first == keys.enable("https://queue.example.test")
    saved = store.load()
    assert saved.line.access_token == "new-access"
    assert saved.line.refresh_token == "new-refresh"
    assert saved.vapid is not None
    assert saved.vapid.private_key not in repr(saved)
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert {p.name for p in tmp_path.iterdir()} == {"state.json", "state.lock"}


def test_retry_schedule_expired_endpoint_and_bounded_batch(tmp_path: Path) -> None:
    repo = NotificationRepository(database(tmp_path))
    identity = repo.subscribe(subscription(), NOW)
    repo.test(identity, NOW)
    for minute, next_minute in [(0, 1), (1, 6), (6, 21)]:
        delivery = repo.pending(NOW + timedelta(minutes=minute))[0]
        repo.result(delivery, "transient", NOW + timedelta(minutes=minute))
        assert repo.pending(NOW + timedelta(minutes=next_minute, seconds=-1)) == []
    delivery = repo.pending(NOW + timedelta(minutes=21))[0]
    repo.result(delivery, "expired", NOW + timedelta(minutes=21))
    with pytest.raises(LookupError):
        repo.test(identity, NOW)
    for n in range(25):
        identity = repo.subscribe(subscription(str(n)), NOW)
        repo.test(identity, NOW)
    assert len(repo.pending(NOW)) == 20


def test_sender_uses_memory_key_fresh_claims_timeout_ttl_and_redacts_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pywebpush
    from requests import Response

    from matoca_service.notifications.keys import VapidKeys
    from matoca_service.notifications.sender import PushSender
    from matoca_service.state.models import AppState, LineState
    from matoca_service.state.store import JsonStateStore

    store = JsonStateStore(tmp_path / "state.json")
    store.save(AppState(line=LineState(access_token="access", refresh_token="refresh", adid="d")))
    keys = VapidKeys(store)
    keys.enable("https://queue.example.test")
    repo = NotificationRepository(database(tmp_path))
    identity = repo.subscribe(subscription(), NOW)
    repo.test(identity, NOW)
    received = []

    def send(**kwargs):
        assert kwargs["timeout"] == 10
        assert kwargs["ttl"] == 900
        assert "aud" not in kwargs["vapid_claims"]
        assert kwargs["vapid_claims"]["sub"] == "https://queue.example.test"
        kwargs["vapid_claims"]["aud"] = "mutated"
        assert not isinstance(kwargs["vapid_private_key"], str)
        received.append(kwargs["data"])
        response = Response()
        response.status_code = 410
        raise pywebpush.WebPushException("SECRET-ENDPOINT", response=response)

    monkeypatch.setattr(pywebpush, "webpush", send)
    sender = PushSender(keys)
    assert sender.send(repo.pending(NOW)[0]) == "expired"
    assert sender.send(repo.pending(NOW)[0]) == "expired"
    assert "SECRET" not in str(received)


def test_vapid_rejects_non_https_subject_before_state_mutation(tmp_path: Path) -> None:
    from matoca_service.notifications.keys import VapidKeys
    from matoca_service.state.models import AppState, LineState
    from matoca_service.state.store import JsonStateStore

    store = JsonStateStore(tmp_path / "state.json")
    store.save(AppState(line=LineState(access_token="access", refresh_token="refresh", adid="d")))
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        VapidKeys(store).enable("http://192.168.1.2")
    assert store.path.read_bytes() == before


def test_sender_blocks_redirects_at_transport_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    from matoca_service.notifications.sender import NoRedirectSession

    captured = []

    def request(self, method, url, **kwargs):
        captured.append(kwargs["allow_redirects"])
        return requests.Response()

    monkeypatch.setattr(requests.Session, "request", request)
    with NoRedirectSession() as session:
        session.post("https://fcm.googleapis.com/fake", allow_redirects=True)
    assert captured == [False]


def test_task_terminal_events_deduplicate_and_include_shop_not_sensitive_decision(
    tmp_path: Path,
) -> None:
    from matoca_service.automation.models import AutomationRequest
    from matoca_service.automation.repository import AutomationRepository

    db = database(tmp_path)
    tasks = AutomationRepository(db)
    task = tasks.create(
        AutomationRequest(
            merchant_key="sawayaka",
            shop_id=1,
            arrival_at=NOW + timedelta(hours=1),
            timezone="Asia/Tokyo",
            consent=True,
        ),
        "合成店舗",
        "private-form",
        NOW,
    )
    for state in ["needs_attention", "needs_attention", "failed", "expired"]:
        task = tasks.transition(task, state, "SENSITIVE-UPSTREAM", NOW)
    history = NotificationRepository(db).history()
    assert len(history) == 3
    assert all("合成店舗" in item.body and "さわやか" in item.body for item in history)
    assert "SENSITIVE" not in str(history)


def test_observation_gap_discards_old_prediction_baseline(tmp_path: Path) -> None:
    db = database(tmp_path)
    queues = QueueRepository(db)
    for minute, count in [(0, 100), (5, 99), (30, 40), (35, 5)]:
        queues.record_read(
            QueueRead(
                merchant_key="sawayaka",
                waiting_id=1,
                shop_id=1,
                observed_at=NOW + timedelta(minutes=minute),
                count=count,
            )
        )
    assert not [n for n in NotificationRepository(db).history() if n.kind == "predicted_earlier"]
    assert all("さわやか" in n.body for n in NotificationRepository(db).history())


@pytest.mark.asyncio
async def test_dispatcher_runs_without_browser_and_keeps_sender_off_event_loop(
    tmp_path: Path,
) -> None:
    import threading
    from typing import cast

    from matoca_service.notifications.dispatcher import NotificationDispatcher
    from matoca_service.notifications.sender import PushSender

    repo = NotificationRepository(database(tmp_path))
    identity = repo.subscribe(subscription(), NOW)
    repo.test(identity, NOW)
    event_thread = threading.get_ident()
    sent = []

    class FakeSender:
        def send(self, delivery):
            assert threading.get_ident() != event_thread
            sent.append(delivery.notification.title)
            return "sent"

    dispatcher = NotificationDispatcher(repo, cast(PushSender, FakeSender()))
    await dispatcher.dispatch_once()
    assert sent == ["通知のテスト"]
    assert repo.pending(NOW.replace(year=2099)) == []
    assert len(repo.history()) == 1
