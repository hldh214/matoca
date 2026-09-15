from datetime import UTC, datetime, timedelta

from matoca_service.console import ShopConsoleItem
from matoca_service.prediction.models import Prediction
from matoca_service.tracking.models import QueueObservation, QueueSession

NOW = datetime(2026, 9, 14, tzinfo=UTC)


def test_console_prediction_is_typed_and_optional() -> None:
    item = ShopConsoleItem(
        id=1,
        name="店",
        sub_name=None,
        address=None,
        image_url=None,
        current_waiting=2,
        official_waiting_minutes=20,
        official_waiting_is_more=False,
        status="available",
        status_label="受付可能",
        can_join=True,
        stale=False,
        updated_at=NOW,
        forms=None,
        prediction=Prediction(
            fast_minutes=15,
            typical_minutes=25,
            confidence="low",
            effective_samples=2.5,
            level="shop",
        ),
    )
    assert item.prediction is not None
    assert item.prediction.typical_minutes == 25


def test_queue_prediction_is_typed_and_optional() -> None:
    session = QueueSession(
        session_id=1,
        merchant_key="merchant",
        shop_id=1,
        waiting_id=1,
        source="manual",
        number=None,
        adult_count=None,
        child_count=None,
        submitted_at=NOW,
        first_observed_at=NOW,
        official_minutes_at_submission=30,
        official_is_more_at_submission=False,
        called_at=None,
        cancelled_at=None,
        status="active",
        observations=[QueueObservation(observed_at=NOW, count=5)],
        prediction=Prediction(
            fast_minutes=20,
            typical_minutes=30,
            confidence="medium",
            effective_samples=8,
            level="shop",
        ),
    )

    assert session.prediction is not None
    assert session.prediction.fast_minutes == 20


def test_active_trajectory_requires_a_decreasing_observation() -> None:
    base = dict(
        session_id=1,
        merchant_key="merchant",
        shop_id=1,
        waiting_id=1,
        source="manual",
        number=None,
        adult_count=None,
        child_count=None,
        submitted_at=NOW,
        first_observed_at=NOW,
        official_minutes_at_submission=30,
        official_is_more_at_submission=False,
        called_at=None,
        cancelled_at=None,
        status="active",
    )
    flat = QueueSession(
        **base,
        observations=[
            QueueObservation(observed_at=NOW, count=5),
            QueueObservation(observed_at=NOW + timedelta(minutes=10), count=5),
        ],
    )
    decreasing = QueueSession(
        **base,
        observations=[
            QueueObservation(observed_at=NOW, count=6),
            QueueObservation(observed_at=NOW + timedelta(minutes=10), count=4),
        ],
    )
    assert flat.trajectory_minutes is None
    assert decreasing.trajectory_minutes == 20
