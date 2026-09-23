from datetime import UTC, datetime, timedelta

from matoca_service.tracking.models import QueueObservation
from matoca_service.tracking.progress import calculate_progress

NOW = datetime(2026, 9, 21, 4, tzinfo=UTC)


def test_progress_compares_groups_with_initial_linear_estimate():
    observations = [
        QueueObservation(observed_at=NOW + timedelta(minutes=i), count=40 - i) for i in range(21)
    ]
    result = calculate_progress(observations, NOW, 100, False)
    assert result.linear_remaining_minutes == 80
    assert result.linear_expected_groups == 32
    assert result.groups_ahead_of_linear == 12
    assert [w.remaining_minutes for w in result.windows] == [20, 20, None]
    assert result.windows[0].groups_per_minute == 1


def test_missing_samples_and_no_progress_do_not_invent_eta():
    observations = [
        QueueObservation(observed_at=NOW, count=10),
        QueueObservation(observed_at=NOW + timedelta(minutes=5), count=5),
    ]
    result = calculate_progress(observations, NOW, 30, False)
    assert result.windows[0].remaining_minutes is None
    flat = [QueueObservation(observed_at=NOW + timedelta(minutes=i), count=5) for i in range(6)]
    result = calculate_progress(flat, NOW, 30, True)
    assert result.linear_remaining_minutes is None
    assert result.windows[0].remaining_minutes is None


def test_missing_initial_count_does_not_invent_initial_group_baseline():
    observations = [
        QueueObservation(observed_at=NOW, count=None),
        QueueObservation(observed_at=NOW + timedelta(minutes=5), count=10),
    ]
    result = calculate_progress(observations, NOW, 30, False)
    assert result.linear_expected_groups is None
