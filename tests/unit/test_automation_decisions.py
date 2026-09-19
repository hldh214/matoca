from datetime import UTC, datetime, timedelta

import pytest

from matoca_service.automation.decisions import evaluate_timing
from matoca_service.prediction.models import Prediction

ARRIVAL = datetime(2026, 9, 19, 9, tzinfo=UTC)


@pytest.mark.parametrize(
    "offset,age,minutes,is_more,fresh,available,expected",
    [
        (-60, 0, None, False, True, True, "estimate_unavailable"),
        (-60, 0, 90, True, True, True, "estimate_unavailable"),
        (0, 0, None, False, True, True, "arrival"),
        (120, 60, 90, True, True, True, "arrival"),
        (121, 0, 30, False, True, True, "expired"),
        (0, 61, 30, False, True, True, "stale"),
        (0, 0, 30, False, False, True, "stale"),
        (0, 0, None, False, True, False, "unavailable"),
        (-1801, 0, 30, False, True, True, "too_early"),
        (-1800, 0, 30, False, True, True, "timing_ready"),
    ],
)
def test_shared_timing_boundaries(offset, age, minutes, is_more, fresh, available, expected):
    now = ARRIVAL + timedelta(seconds=offset)
    decision = evaluate_timing(
        evaluated_at=now,
        checked_at=now - timedelta(seconds=age),
        arrival_at=ARRIVAL,
        official_minutes=minutes,
        official_is_more=is_more,
        prediction=Prediction(
            fast_minutes=30,
            typical_minutes=30,
            confidence="low",
            effective_samples=0,
            level="cold_start",
        ),
        early_tolerance_minutes=15,
        model_error_minutes=15,
        available=available,
        observation_fresh=fresh,
    )
    assert decision.reason_code == expected
    assert decision.would_submit is (expected in {"arrival", "timing_ready"})
