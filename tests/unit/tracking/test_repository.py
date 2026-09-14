import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from matoca_service.storage.database import Database
from matoca_service.tracking.models import QueueIntent, QueueRead
from matoca_service.tracking.repository import QueueRepository

NOW = datetime(2026, 9, 13, 3, 4, 20, tzinfo=UTC)


def repository(tmp_path: Path) -> QueueRepository:
    database = Database(tmp_path / "data" / "matoca.sqlite3")
    database.initialize()
    return QueueRepository(database)


def intent() -> QueueIntent:
    return QueueIntent(
        intent_id="intent-1",
        merchant_key="sawayaka",
        shop_id=3272,
        submitted_at=NOW,
        official_minutes_at_submission=25,
        adult_count=2,
        child_count=0,
    )


def test_observed_zero_confirms_call_and_is_not_reopened(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.begin_intent(intent())
    repo.resolve_intent("intent-1", waiting_id=9001, number=42, count=5, observed_at=NOW)

    repo.record_read(
        QueueRead(
            merchant_key="sawayaka",
            observed_at=NOW + timedelta(minutes=1),
            waiting_id=9001,
            count=0,
        )
    )
    repo.record_read(
        QueueRead(
            merchant_key="sawayaka",
            observed_at=NOW + timedelta(minutes=2),
            waiting_id=9001,
            count=4,
        )
    )

    session = repo.list_sessions()[0]
    assert session.status == "called"
    assert session.called_at == datetime(2026, 9, 13, 3, 5, tzinfo=UTC)


def test_successful_disappearance_is_unknown_not_called(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.begin_intent(intent())
    repo.resolve_intent("intent-1", waiting_id=9001, number=42, count=5, observed_at=NOW)

    repo.record_merchant_read("sawayaka", NOW + timedelta(minutes=1), [])

    session = repo.list_sessions()[0]
    assert session.status == "unknown"
    assert session.called_at is None


def test_explicit_cancellation_stays_terminal_after_old_read(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.begin_intent(intent())
    repo.resolve_intent("intent-1", waiting_id=9001, number=42, count=5, observed_at=NOW)
    repo.mark_cancelled("sawayaka", 9001, NOW + timedelta(minutes=2))

    repo.record_read(
        QueueRead(
            merchant_key="sawayaka",
            observed_at=NOW + timedelta(minutes=1),
            waiting_id=9001,
            count=3,
        )
    )

    assert repo.list_sessions()[0].status == "cancelled"


def test_failed_read_only_marks_active_session_stale(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.begin_intent(intent())
    repo.resolve_intent("intent-1", waiting_id=9001, number=42, count=5, observed_at=NOW)

    repo.record_failure("sawayaka", NOW + timedelta(minutes=1), "timeout")

    session = repo.list_sessions()[0]
    assert session.status == "active"
    assert session.stale is True
    assert session.error_code == "timeout"


def test_restart_recovers_active_session_and_minute_observation_is_unique(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "matoca.sqlite3")
    database.initialize()
    first = QueueRepository(database)
    first.begin_intent(intent())
    first.resolve_intent("intent-1", waiting_id=9001, number=42, count=5, observed_at=NOW)
    first.record_read(QueueRead(merchant_key="sawayaka", observed_at=NOW, waiting_id=9001, count=4))
    first.record_read(
        QueueRead(
            merchant_key="sawayaka",
            observed_at=NOW + timedelta(seconds=35),
            waiting_id=9001,
            count=3,
        )
    )

    restarted = QueueRepository(database)
    session = restarted.active_sessions()[0]
    assert [item.count for item in session.observations] == [3]


def test_external_ticket_is_adopted_without_inventing_submission_time(tmp_path: Path) -> None:
    repo = repository(tmp_path)

    repo.record_read(
        QueueRead(
            merchant_key="la_ohana_yokohamahonmoku",
            observed_at=NOW,
            waiting_id=8001,
            shop_id=15,
            number=7,
            count=3,
            adult_count=2,
            child_count=1,
        )
    )

    session = repo.list_sessions()[0]
    assert session.source == "adopted"
    assert session.submitted_at is None
    assert session.official_minutes_at_submission is None


def test_lower_bound_official_estimate_survives_restart(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    submitted = intent().model_copy(update={"official_is_more_at_submission": True})
    repo.begin_intent(submitted)
    repo.resolve_intent("intent-1", waiting_id=9001, number=42, count=5, observed_at=NOW)

    session = repository(tmp_path).list_sessions()[0]
    assert session.official_minutes_at_submission == 25
    assert session.official_is_more_at_submission is True


def test_unresolved_intent_blocks_another_submission(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.begin_intent(intent())
    repo.mark_intent_unresolved("intent-1", "send_outcome_unknown")

    with pytest.raises(sqlite3.IntegrityError):
        repo.begin_intent(intent().model_copy(update={"intent_id": "intent-2"}))


def test_restart_reconciles_matching_waiting_to_original_intent(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.begin_intent(intent().model_copy(update={"source": "automation"}))
    repo.mark_intent_unresolved("intent-1", "send_outcome_unknown")

    restarted = repository(tmp_path)
    restarted.record_read(
        QueueRead(
            merchant_key="sawayaka",
            observed_at=NOW,
            waiting_id=9001,
            shop_id=3272,
            number=42,
            count=5,
            adult_count=2,
            child_count=0,
        )
    )

    session = restarted.list_sessions()[0]
    assert session.intent_id == "intent-1"
    assert session.source == "automation"
    assert session.submitted_at == NOW


def test_stale_unresolved_intent_does_not_capture_later_external_ticket(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.begin_intent(intent())
    repo.mark_intent_unresolved("intent-1", "send_outcome_unknown")

    repo.record_read(
        QueueRead(
            merchant_key="sawayaka",
            observed_at=NOW + timedelta(hours=1),
            waiting_id=9002,
            shop_id=3272,
            count=4,
            adult_count=2,
            child_count=0,
        )
    )

    session = repo.list_sessions()[0]
    assert session.intent_id is None
    assert session.source == "adopted"
    assert session.submitted_at is None


def test_definite_rejection_releases_submission_block(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.begin_intent(intent())

    repo.mark_intent_failed("intent-1", "upstream_rejected")
    repo.begin_intent(intent().model_copy(update={"intent_id": "intent-2"}))


def test_cancellation_request_prevents_older_zero_from_becoming_training_evidence(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    repo.begin_intent(intent())
    repo.resolve_intent("intent-1", waiting_id=9001, number=42, count=5, observed_at=NOW)
    repo.mark_cancellation_requested("sawayaka", 9001, NOW + timedelta(minutes=2))

    repo.record_read(
        QueueRead(
            merchant_key="sawayaka",
            observed_at=NOW + timedelta(minutes=1),
            waiting_id=9001,
            count=0,
        )
    )

    session = repo.list_sessions()[0]
    assert session.status == "active"
    assert session.called_at is None
