from datetime import UTC, datetime, timedelta

from matoca_service.matoca.models import Waiting
from matoca_service.prediction.repository import PredictionRepository, PredictionService
from matoca_service.storage.database import Database
from matoca_service.tracking.models import QueueIntent
from matoca_service.tracking.repository import QueueRepository

NOW = datetime(2026, 9, 21, 4, tzinfo=UTC)


def setup_queue(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    queues = QueueRepository(db)
    queues.begin_intent(
        QueueIntent(
            intent_id="test",
            merchant_key="sawayaka",
            shop_id=3314,
            submitted_at=NOW,
            official_minutes_at_submission=96,
            official_is_more_at_submission=False,
            adult_count=2,
            child_count=0,
        )
    )
    session = queues.resolve_intent(
        "test", waiting_id=1, number=147, count=38, observed_at=NOW, status=2
    )
    return db, queues, session.session_id


def test_raw_status_and_pre_call_are_saved_without_inventing_call(tmp_path):
    db, queues, _ = setup_queue(tmp_path)
    for minutes, status, count in [(66, 8, 7), (90, 5, 36)]:
        queues.record_waiting(
            "sawayaka",
            NOW + timedelta(minutes=minutes),
            [Waiting(id=1, count=count, status=status)],
        )
    session = queues.list_sessions()[0]
    assert session.observations[-1].raw_status == 5
    assert session.called_at is None
    assert "progress" not in session.model_dump()
    samples = PredictionRepository(db).samples(
        "sawayaka", NOW + timedelta(hours=2), target="pre_call"
    )
    assert len(samples) == 1
    assert samples[0].ratio == 66 / 96
    assert PredictionRepository(db).samples("sawayaka", NOW + timedelta(hours=2)) == []


def test_manual_notification_is_separate_and_not_visible_before_confirmation(tmp_path):
    db, queues, session_id = setup_queue(tmp_path)
    at = NOW + timedelta(hours=3)
    queues.confirm_notification(session_id, "pre_call", NOW + timedelta(minutes=66), at)
    queues.confirm_notification(session_id, "calling", NOW + timedelta(minutes=90), at)
    queues.mark_cancelled("sawayaka", 1, NOW + timedelta(minutes=121))
    repo = PredictionRepository(db)
    assert repo.samples("sawayaka", at - timedelta(seconds=1), target="pre_call") == []
    assert repo.samples("sawayaka", at, target="pre_call")[0].ratio == 66 / 96
    assert repo.samples("sawayaka", at)[0].ratio == 90 / 96
    assert queues.list_sessions()[0].called_at is None
    forecast = PredictionService(repo).predict_pre_call("sawayaka", 3314, 96, at)
    assert forecast.target == "pre_call"
    assert forecast.fast_minutes == 66
    assert forecast.confidence == "low"


def test_pre_call_cold_start_does_not_use_official_call_duration(tmp_path):
    db, _, _ = setup_queue(tmp_path)
    prediction = PredictionService(PredictionRepository(db)).predict_pre_call(
        "sawayaka", 3314, 96, NOW
    )
    assert prediction.fast_minutes == 0
    assert prediction.effective_samples == 0


def test_status_changes_within_one_minute_remain_available_and_stop_trends(tmp_path):
    db, queues, session_id = setup_queue(tmp_path)
    queues.record_waiting(
        "sawayaka", NOW + timedelta(seconds=10), [Waiting(id=1, count=7, status=8)]
    )
    queues.record_waiting(
        "sawayaka", NOW + timedelta(seconds=20), [Waiting(id=1, count=36, status=5)]
    )
    states = db.read(
        lambda c: c.execute(
            """SELECT raw_status FROM queue_status_observations
               WHERE session_id=? ORDER BY observed_at""",
            (session_id,),
        ).fetchall()
    )
    assert states == [(2,), (8,), (5,)]
    assert "trajectory_minutes" not in queues.list_sessions()[0].model_dump()
    assert "progress" not in queues.list_sessions()[0].model_dump()
