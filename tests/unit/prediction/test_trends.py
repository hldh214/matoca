from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from matoca_service.prediction.trends import TrendObservation, TrendPair, TrendTimeline, build_pairs

START = datetime(2026, 9, 14, 1, tzinfo=UTC)  # Monday, Tokyo 10:00


def observations(start=START, *, shop_id=1, drop=10):
    return [
        TrendObservation(shop_id, start + timedelta(minutes=i), 40 - drop if i == 4 else 40)
        for i in range(5)
    ]


def evidence(count=20, *, start=START, shop_id=1, drop=10):
    return [
        point
        for i in range(count)
        for point in observations(start + timedelta(minutes=6 * i), shop_id=shop_id, drop=drop)
    ]


def test_nonoverlapping_intervals_and_past_only_threshold():
    pairs = build_pairs(evidence())
    assert len(pairs) == 20
    assert all(left.end < right.start for left, right in pairwise(pairs))
    timeline = TrendTimeline(pairs)
    before = timeline.summary(1, START + timedelta(minutes=117))
    assert before.sample_count == 19
    assert before.suggested_addition_minutes is None
    complete = timeline.summary(1, START + timedelta(minutes=119))
    assert complete.sample_count == complete.downward_count == 20
    assert complete.source == "shop_daypart"
    assert complete.suggested_addition_minutes == 10


@pytest.mark.parametrize("bad", [None, -1, 0, "invalid"])
def test_bad_intermediate_observation_breaks_interval(bad):
    points = observations()
    points[2] = TrendObservation(
        1, START + timedelta(minutes=2), 40 if bad == "invalid" else bad, bad != "invalid"
    )
    assert build_pairs(points) == []


def test_gaps_and_horizon_boundaries_and_no_cross_shop_pairs():
    assert len(build_pairs(observations()[:5])) == 1  # four minutes
    assert build_pairs(observations()[:4]) == []
    points = observations()
    assert build_pairs([points[0], points[3], points[4]]) == []
    six = [TrendObservation(1, START + timedelta(minutes=i), 40 - i) for i in (0, 2, 4, 6)]
    assert len(build_pairs(six)) == 1
    assert build_pairs([points[0], TrendObservation(2, points[4].at, 20)]) == []


def test_scope_fallback_uses_tokyo_start_daypart_and_day_class():
    pairs = build_pairs(evidence(start=START - timedelta(days=2), shop_id=2))
    summary = TrendTimeline(pairs).summary(1, START)
    assert summary.source == "merchant"
    assert summary.sample_count == 20
    assert summary.suggested_addition_minutes == 10
    shop = TrendTimeline(pairs).summary(2, START)
    assert shop.source == "shop"  # Saturday history cannot count as Monday daypart
    assert shop.scopes[0].sample_count == 0


def test_thirty_day_window_and_future_large_drops_do_not_leak():
    now = START + timedelta(days=1)
    pairs = build_pairs(evidence() + evidence(start=now + timedelta(minutes=1), drop=35))
    timeline = TrendTimeline(pairs)
    assert timeline.summary(1, now).suggested_addition_minutes == 10
    assert timeline.summary(1, now + timedelta(minutes=121)).suggested_addition_minutes == 35
    assert timeline.summary(1, now + timedelta(days=31)).sample_count == 0


def test_positive_changes_are_zero_in_nonnegative_nearest_rank_p90():
    points = evidence(18, drop=-5) + evidence(2, start=START + timedelta(minutes=108), drop=20)
    summary = TrendTimeline(build_pairs(points)).summary(1, START + timedelta(minutes=119))
    assert summary.sample_count == 20
    assert summary.downward_count == 2
    assert summary.p90_downward_minutes == 0
    assert summary.suggested_addition_minutes == 0


def test_thirty_day_cutoff_is_inclusive_on_start_and_expiry_orders_by_start():
    now = START + timedelta(days=30)
    pairs = [
        TrendPair(1, START - timedelta(minutes=1), START + timedelta(minutes=5), -30),
        TrendPair(2, START, START + timedelta(minutes=4), -5),
    ]
    timeline = TrendTimeline(pairs)
    assert timeline.summary(1, now).sample_count == 1
    assert timeline.summary(1, now).p90_downward_minutes == 5
    assert timeline.summary(1, now + timedelta(microseconds=1)).sample_count == 0


def test_tokyo_weekend_and_bucket_use_pair_start_not_end():
    # Friday 23:58 in Tokyo, ending Saturday 00:02.
    start = datetime(2026, 9, 18, 14, 58, tzinfo=UTC)
    pair = TrendPair(1, start, start + timedelta(minutes=4), -5)
    summary = TrendTimeline([pair]).summary(1, pair.end)
    assert summary.day_class == "weekend"
    assert summary.tokyo_bucket_start == 0
    assert summary.scopes[0].sample_count == 0
    assert summary.scopes[1].sample_count == 1


def test_adding_later_rows_never_changes_selected_pair_prefix():
    points = evidence()
    prefix = build_pairs(points[:48])
    later = build_pairs(points)
    assert prefix == [pair for pair in later if pair.end <= points[47].at]
