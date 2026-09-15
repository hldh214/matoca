from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import matoca_service.service as service_module
from matoca_service.matoca.models import Shop, WaitingEstimate
from matoca_service.service import MatocaService
from matoca_service.storage.models import CollectionWrite, ShopObservation
from matoca_service.storage.repositories import ShopRepository
from matoca_service.tracking.models import QueueObservation, QueueSession
from matoca_service.tracking.repository import QueueRepository


class FixedDateTime(datetime):
    now_value: datetime

    @classmethod
    def now(cls, tz=None):
        return cls.now_value if tz is not None else cls.now_value.replace(tzinfo=None)


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MatocaService, datetime]:
    line_client_path = tmp_path / "line_client.toml"
    line_client_path.write_text(
        "\n".join(
            [
                'host = "invalid.example"',
                'application = "synthetic-app"',
                'locale = "ja_JP"',
                'protocol_version = "1"',
                'user_agent = "synthetic-agent"',
            ]
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 9, 15, 3, tzinfo=UTC)
    FixedDateTime.now_value = now
    monkeypatch.setattr(service_module, "datetime", FixedDateTime)
    result = MatocaService(line_client_path, tmp_path / "state.json", tmp_path / "data.sqlite3")

    def seed_sample(connection):
        submitted = now - timedelta(days=1, minutes=20)
        called = now - timedelta(days=1)
        cursor = connection.execute(
            """INSERT INTO queue_sessions
            (merchant_key, shop_id, waiting_id, source, submitted_at, first_observed_at,
             official_minutes_at_submission, official_is_more_at_submission, called_at, status)
            VALUES ('sawayaka', 3272, 9001, 'manual', ?, ?, 20, 0, ?, 'called')""",
            (submitted.isoformat(), submitted.isoformat(), called.isoformat()),
        )
        connection.execute(
            "INSERT INTO queue_session_observations VALUES (?, ?, 0)",
            (cursor.lastrowid, called.isoformat()),
        )

    result._database.write(seed_sample)
    return result, now


def save_observation(
    service: MatocaService,
    at: datetime,
    *,
    minutes: int | None,
    is_more: bool = False,
    detail_fresh: bool = True,
    error_code: str | None = None,
) -> None:
    ShopRepository(service._database).save_cycle(
        CollectionWrite(
            merchant_key="sawayaka",
            observed_at=at,
            shops=[
                ShopObservation(
                    shop=Shop(
                        id=3272,
                        name="Synthetic Shop",
                        current_waiting=3,
                        waiting_time=(
                            WaitingEstimate(minutes=minutes, is_more=is_more)
                            if minutes is not None
                            else None
                        ),
                        is_open=True,
                        is_issuable=True,
                    ),
                    list_fresh=True,
                    detail_fresh=detail_fresh,
                    error_code=error_code,
                )
            ],
        )
    )


@pytest.mark.asyncio
async def test_console_enriches_fresh_exact_estimate_and_suppresses_stale_or_lower_bound(
    service: tuple[MatocaService, datetime],
) -> None:
    app, now = service
    save_observation(app, now, minutes=30)
    assert (await app.merchant_console("sawayaka")).shops[0].prediction is not None

    save_observation(app, now + timedelta(minutes=1), minutes=30, is_more=True)
    assert (await app.merchant_console("sawayaka")).shops[0].prediction is None

    save_observation(
        app, now + timedelta(minutes=2), minutes=None, detail_fresh=False, error_code="timeout"
    )
    assert (await app.merchant_console("sawayaka")).shops[0].prediction is None


@pytest.mark.asyncio
async def test_history_enriches_only_exact_successful_observations(
    service: tuple[MatocaService, datetime],
) -> None:
    app, now = service
    save_observation(app, now - timedelta(minutes=2), minutes=30)
    save_observation(app, now - timedelta(minutes=1), minutes=30, is_more=True)
    save_observation(app, now, minutes=None, detail_fresh=False, error_code="timeout")

    history = await app.shop_history("sawayaka", 3272, date(2026, 9, 15))

    assert [item.prediction is not None for item in history.observations] == [True, False, False]


@pytest.mark.asyncio
async def test_queues_enrich_only_fresh_active_exact_sessions(
    service: tuple[MatocaService, datetime],
) -> None:
    app, now = service
    save_observation(app, now, minutes=30)

    def seed_sessions(connection):
        rows = [(9101, "active", 0), (9102, "called", 0), (9103, "active", 1)]
        for waiting_id, status, is_more in rows:
            connection.execute(
                """INSERT INTO queue_sessions
                (merchant_key, shop_id, waiting_id, source, submitted_at, first_observed_at,
                 official_minutes_at_submission, official_is_more_at_submission, called_at, status)
                VALUES ('sawayaka', 3272, ?, 'manual', ?, ?, 30, ?, ?, ?)""",
                (
                    waiting_id,
                    (now - timedelta(minutes=5)).isoformat(),
                    (now - timedelta(minutes=5)).isoformat(),
                    is_more,
                    now.isoformat() if status == "called" else None,
                    status,
                ),
            )

    app._database.write(seed_sessions)
    queues = await app.queues()
    predictions = {
        item.waiting_id: item.prediction for item in queues if hasattr(item, "waiting_id")
    }
    assert predictions[9101] is not None
    assert predictions[9102] is None
    assert predictions[9103] is None

    QueueRepository(app._database).record_failure("sawayaka", now, "timeout")
    stale = await app.queues()
    assert all(item.prediction is None for item in stale if hasattr(item, "prediction"))


def test_active_trajectory_requires_a_decreasing_observation() -> None:
    now = datetime(2026, 9, 15, 3, tzinfo=UTC)
    fields = {
        "session_id": 1,
        "merchant_key": "sawayaka",
        "shop_id": 3272,
        "waiting_id": 1,
        "source": "manual",
        "number": None,
        "adult_count": None,
        "child_count": None,
        "submitted_at": now,
        "first_observed_at": now,
        "official_minutes_at_submission": 30,
        "official_is_more_at_submission": False,
        "called_at": None,
        "cancelled_at": None,
        "status": "active",
    }
    flat = QueueSession(
        **fields,
        observations=[
            QueueObservation(observed_at=now, count=5),
            QueueObservation(observed_at=now + timedelta(minutes=10), count=5),
        ],
    )
    decreasing = QueueSession(
        **fields,
        observations=[
            QueueObservation(observed_at=now, count=6),
            QueueObservation(observed_at=now + timedelta(minutes=10), count=4),
        ],
    )

    assert flat.trajectory_minutes is None
    assert decreasing.trajectory_minutes == 20
