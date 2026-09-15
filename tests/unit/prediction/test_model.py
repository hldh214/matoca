import math
from datetime import UTC, datetime, timedelta

import pytest

from matoca_service.prediction.model import predict, remaining
from matoca_service.prediction.models import Prediction, PredictionSample

NOW = datetime(2026, 9, 14, tzinfo=UTC)


def sample(
    ratio: float, *, age_days: float = 0, shop_id: int = 1, at: datetime = NOW
) -> PredictionSample:
    return PredictionSample(
        merchant_key="merchant",
        shop_id=shop_id,
        submitted_at=at - timedelta(days=age_days),
        ratio=ratio,
    )


def test_cold_start_and_null_official_estimate() -> None:
    assert predict("merchant", 1, None, NOW, []) is None
    result = predict("merchant", 1, 30, NOW, [])
    assert result is not None
    assert result.model_dump() == {
        "fast_minutes": 30,
        "typical_minutes": 30,
        "confidence": "low",
        "effective_samples": 0.0,
        "level": "cold_start",
    }


def test_weighted_quantiles_and_30_day_half_life() -> None:
    samples = [sample(0.5), sample(1.0), sample(2.0, age_days=30)]
    result = predict("merchant", 99, 40, NOW, samples)
    assert result is not None
    assert result.fast_minutes == 20
    assert result.typical_minutes == 40
    assert result.effective_samples == pytest.approx(2.5)
    assert result.level == "merchant"


def test_prediction_rounds_fractional_minutes_up_and_accepts_zero_ratios() -> None:
    result = predict("merchant", 99, 3, NOW, [sample(0), sample(0.34)])
    assert result is not None
    assert result.fast_minutes == 0
    assert result.typical_minutes == 0

    result = predict("merchant", 99, 3, NOW, [sample(0.34)])
    assert result is not None
    assert result.fast_minutes == 2
    assert result.typical_minutes == 2


def test_active_prediction_reports_remaining_minutes() -> None:
    prediction = Prediction(
        fast_minutes=20,
        typical_minutes=30,
        confidence="medium",
        effective_samples=8,
        level="shop",
    )

    result = remaining(prediction, NOW, NOW + timedelta(minutes=12, seconds=1))

    assert result.fast_minutes == 8
    assert result.typical_minutes == 18


def test_shop_and_tokyo_daypart_quantiles_shrink_toward_parent() -> None:
    # NOW is Monday 09:00 JST. Five local ratios at the selected shop/daypart
    # shrink halfway toward the merchant parent because n/(n+10) = 1/3.
    samples = [sample(1.0, shop_id=2) for _ in range(10)] + [sample(2.0) for _ in range(5)]
    result = predict("merchant", 1, 60, NOW, samples)
    assert result is not None
    assert result.level == "shop_daypart"
    assert result.effective_samples == pytest.approx(5)
    # merchant=1, shop=(1*2/3 + 2*1/3)=4/3, daypart=(4/3*2/3 + 2*1/3)=14/9
    assert result.fast_minutes == 94
    assert result.typical_minutes == 94
    assert result.confidence == "medium"


def test_tokyo_day_class_and_three_hour_bucket_select_only_matching_samples() -> None:
    local = [sample(2.0) for _ in range(5)]
    other_bucket = sample(4.0, at=NOW - timedelta(hours=3))
    weekend_same_bucket = sample(6.0, at=NOW - timedelta(days=1))
    merchant_parent = [sample(1.0, shop_id=2) for _ in range(10)]

    result = predict(
        "merchant",
        1,
        60,
        NOW,
        [*merchant_parent, *local, other_bucket, weekend_same_bucket],
    )

    assert result is not None
    assert result.level == "shop_daypart"
    assert result.effective_samples == pytest.approx(5)
    assert result.fast_minutes == 97
    assert result.typical_minutes == 97


def test_shop_samples_outside_tokyo_daypart_fall_back_to_shop_level() -> None:
    samples = [sample(1.0, shop_id=2) for _ in range(10)] + [
        sample(2.0, at=NOW - timedelta(hours=3)) for _ in range(5)
    ]

    result = predict("merchant", 1, 60, NOW, samples)

    assert result is not None
    assert result.level == "shop"
    assert result.effective_samples == pytest.approx(5 * math.exp2(-0.125 / 30))
    assert result.fast_minutes == 80
    assert result.typical_minutes == 80


@pytest.mark.parametrize(("count", "confidence"), [(4, "low"), (5, "medium"), (20, "high")])
def test_confidence_boundaries(count: int, confidence: str) -> None:
    result = predict("merchant", 1, 10, NOW, [sample(1.0) for _ in range(count)])
    assert result is not None
    assert result.confidence == confidence
