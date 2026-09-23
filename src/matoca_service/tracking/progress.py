from datetime import datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from matoca_service.tracking.models import QueueObservation


class ProgressWindow(BaseModel):
    minutes: int
    groups_per_minute: float | None = None
    remaining_minutes: int | None = None


class QueueProgress(BaseModel):
    linear_remaining_minutes: float | None = None
    linear_expected_groups: float | None = None
    groups_ahead_of_linear: float | None = None
    windows: list[ProgressWindow]


def calculate_progress(
    observations: list[QueueObservation],
    submitted_at: datetime | None,
    initial_minutes: int | None,
    initial_is_more: bool | None,
) -> QueueProgress:
    result = QueueProgress(windows=[ProgressWindow(minutes=m) for m in (5, 15, 30)])
    if not observations:
        return result
    if any(o.raw_status not in (None, 2) for o in observations):
        return result
    latest = observations[-1]
    if submitted_at and initial_minutes and initial_minutes > 0 and initial_is_more is False:
        elapsed = max(0, (latest.observed_at - submitted_at).total_seconds() / 60)
        remaining = max(0, initial_minutes - elapsed)
        result.linear_remaining_minutes = round(remaining, 1)
        first = observations[0]
        if (
            first.count is not None
            and abs((first.observed_at - submitted_at).total_seconds()) <= 60
        ):
            result.linear_expected_groups = round(first.count * remaining / initial_minutes, 1)
            if latest.count is not None:
                result.groups_ahead_of_linear = round(
                    result.linear_expected_groups - latest.count, 1
                )
    if latest.count is None or latest.count <= 0:
        return result
    for window in result.windows:
        cutoff = latest.observed_at - timedelta(minutes=window.minutes)
        samples = [o for o in observations if o.observed_at >= cutoff]
        if len(samples) < 2 or samples[0].observed_at > cutoff:
            continue
        if any(o.count is None for o in samples):
            continue
        if any((b.observed_at - a.observed_at).total_seconds() > 120 for a, b in pairwise(samples)):
            continue
        first_count = samples[0].count
        assert first_count is not None
        decrease = first_count - latest.count
        if decrease <= 0:
            continue
        rate = decrease / window.minutes
        window.groups_per_minute = round(rate, 3)
        window.remaining_minutes = round(latest.count / rate)
    return result
