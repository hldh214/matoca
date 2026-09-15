import math
from datetime import datetime
from zoneinfo import ZoneInfo

from matoca_service.prediction.models import (
    Prediction,
    PredictionConfidence,
    PredictionLevel,
    PredictionSample,
)

TOKYO = ZoneInfo("Asia/Tokyo")
HALF_LIFE_DAYS = 30.0
SHRINKAGE_SAMPLES = 10.0


def _daypart(value: datetime) -> tuple[bool, int]:
    local = value.astimezone(TOKYO)
    return local.weekday() >= 5, local.hour // 3


def _weighted_quantile(values: list[tuple[float, float]], quantile: float) -> float:
    ordered = sorted(values)
    threshold = sum(weight for _, weight in ordered) * quantile
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def _statistics(samples: list[tuple[PredictionSample, float]]) -> tuple[float, float, float]:
    values = [(sample.ratio, weight) for sample, weight in samples]
    return (
        _weighted_quantile(values, 0.2),
        _weighted_quantile(values, 0.5),
        sum(weight for _, weight in samples),
    )


def _shrink(local: float, parent: float, count: float) -> float:
    weight = count / (count + SHRINKAGE_SAMPLES)
    return local * weight + parent * (1.0 - weight)


def remaining(prediction: Prediction, submitted_at: datetime, at: datetime) -> Prediction:
    elapsed_minutes = max(0.0, (at - submitted_at).total_seconds() / 60)
    return prediction.model_copy(
        update={
            "fast_minutes": max(0, math.ceil(prediction.fast_minutes - elapsed_minutes)),
            "typical_minutes": max(0, math.ceil(prediction.typical_minutes - elapsed_minutes)),
        }
    )


def predict(
    merchant_key: str,
    shop_id: int,
    official_minutes: int | None,
    at: datetime,
    samples: list[PredictionSample],
) -> Prediction | None:
    if official_minutes is None or official_minutes < 0:
        return None
    weighted = [
        (sample, math.exp2(-((at - sample.submitted_at).total_seconds() / 86400) / HALF_LIFE_DAYS))
        for sample in samples
        if sample.merchant_key == merchant_key and sample.submitted_at <= at
    ]
    if not weighted:
        return Prediction(
            fast_minutes=official_minutes,
            typical_minutes=official_minutes,
            confidence="low",
            effective_samples=0.0,
            level="cold_start",
        )

    fast, typical, selected_count = _statistics(weighted)
    level: PredictionLevel = "merchant"
    shop = [(sample, weight) for sample, weight in weighted if sample.shop_id == shop_id]
    if shop:
        shop_fast, shop_typical, shop_count = _statistics(shop)
        fast = _shrink(shop_fast, fast, shop_count)
        typical = _shrink(shop_typical, typical, shop_count)
        selected_count = shop_count
        level = "shop"
        target_daypart = _daypart(at)
        local = [item for item in shop if _daypart(item[0].submitted_at) == target_daypart]
        if local:
            local_fast, local_typical, local_count = _statistics(local)
            fast = _shrink(local_fast, fast, local_count)
            typical = _shrink(local_typical, typical, local_count)
            selected_count = local_count
            level = "shop_daypart"

    confidence: PredictionConfidence = (
        "high" if selected_count >= 20 else "medium" if selected_count >= 5 else "low"
    )
    return Prediction(
        fast_minutes=max(0, math.ceil(official_minutes * fast)),
        typical_minutes=max(0, math.ceil(official_minutes * typical)),
        confidence=confidence,
        effective_samples=selected_count,
        level=level,
    )
