"""Past-only official-estimate volatility; never a model of actual call times."""

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from heapq import heappop, heappush
from math import ceil
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

TOKYO = ZoneInfo("Asia/Tokyo")
Scope = Literal["shop_daypart", "shop", "merchant"]


class TrendScope(BaseModel):
    source: Scope
    sample_count: int
    downward_count: int
    p90_downward_minutes: int | None


class TrendSummary(TrendScope):
    as_of: datetime
    day_class: Literal["weekday", "weekend"]
    tokyo_bucket_start: int
    suggested_addition_minutes: int | None
    scopes: list[TrendScope]
    recent_change_minutes: int | None = None
    recent_start_at: datetime | None = None
    recent_end_at: datetime | None = None
    limitation: str = (
        "約5分間の公式目安の変動に備える余裕です。実測待ち時間・早い呼び出しの保証・"
        "受付終了時刻の予測ではありません。過去30日、重複しない有効区間20件以上で提案します。"
    )


@dataclass(frozen=True)
class TrendObservation:
    shop_id: int
    at: datetime
    minutes: int | None
    valid: bool = True


@dataclass(frozen=True)
class TrendPair:
    shop_id: int
    start: datetime
    end: datetime
    change: int

    @property
    def drop(self) -> int:
        return max(0, -self.change)


def build_pairs(observations: list[TrendObservation]) -> list[TrendPair]:
    """Greedy first endpoint in 4-6 min; disjoint rows, prefix-stable selection.

    Zero estimates are conservatively excluded to avoid reception-reset evidence.
    Every intermediate row must remain eligible; individual gaps cannot exceed 2 min.
    """
    pairs = []
    starts: dict[int, TrendObservation] = {}
    previous: dict[int, TrendObservation] = {}
    for point in sorted(observations, key=lambda item: (item.at, item.shop_id)):
        last = previous.get(point.shop_id)
        previous[point.shop_id] = point
        if not point.valid or point.minutes is None or point.minutes <= 0:
            starts.pop(point.shop_id, None)
            continue
        if last and point.at - last.at > timedelta(minutes=2):
            starts.pop(point.shop_id, None)
        start = starts.setdefault(point.shop_id, point)
        duration = point.at - start.at
        if timedelta(minutes=4) <= duration <= timedelta(minutes=6):
            assert start.minutes is not None
            pairs.append(
                TrendPair(point.shop_id, start.at, point.at, point.minutes - start.minutes)
            )
            del starts[point.shop_id]
        elif duration > timedelta(minutes=6):
            starts[point.shop_id] = point
    return pairs


def _bucket(at: datetime) -> tuple[bool, int]:
    local = at.astimezone(TOKYO)
    return local.weekday() >= 5, local.hour // 3 * 3


class TrendTimeline:
    """Advance once through pairs; counters avoid scanning raw history per minute."""

    def __init__(self, pairs: list[TrendPair]) -> None:
        self._pending = deque(sorted(pairs, key=lambda pair: pair.end))
        self._active: list[tuple[datetime, int, TrendPair]] = []
        self._sequence = 0
        self._counts: dict[tuple[object, ...], Counter[int]] = defaultdict(Counter)
        self._latest: dict[int, TrendPair] = {}
        self._at: datetime | None = None

    def _keys(self, pair: TrendPair) -> tuple[tuple[object, ...], ...]:
        return ((), (pair.shop_id,), (pair.shop_id, *_bucket(pair.start)))

    def summary(self, shop_id: int, as_of: datetime) -> TrendSummary:
        if self._at is not None and as_of < self._at:
            raise ValueError("trend timeline must advance chronologically")
        self._at = as_of
        cutoff = as_of - timedelta(days=30)
        while self._pending and self._pending[0].end <= as_of:
            pair = self._pending.popleft()
            self._latest[pair.shop_id] = pair
            heappush(self._active, (pair.start, self._sequence, pair))
            self._sequence += 1
            for key in self._keys(pair):
                self._counts[key][pair.drop] += 1
        while self._active and self._active[0][0] < cutoff:
            _, _, pair = heappop(self._active)
            for key in self._keys(pair):
                self._counts[key][pair.drop] -= 1
                if not self._counts[key][pair.drop]:
                    del self._counts[key][pair.drop]
        weekend, bucket = _bucket(as_of)
        scopes = [
            self._scope("shop_daypart", (shop_id, weekend, bucket)),
            self._scope("shop", (shop_id,)),
            self._scope("merchant", ()),
        ]
        selected = next((scope for scope in scopes if scope.sample_count >= 20), scopes[-1])
        recent = self._latest.get(shop_id)
        if recent and as_of - recent.end > timedelta(minutes=2):
            recent = None
        return TrendSummary(
            **selected.model_dump(),
            as_of=as_of,
            day_class="weekend" if weekend else "weekday",
            tokyo_bucket_start=bucket,
            suggested_addition_minutes=(
                selected.p90_downward_minutes if selected.sample_count >= 20 else None
            ),
            scopes=scopes,
            recent_change_minutes=recent.change if recent else None,
            recent_start_at=recent.start if recent else None,
            recent_end_at=recent.end if recent else None,
        )

    def _scope(self, source: Scope, key: tuple[object, ...]) -> TrendScope:
        counts = self._counts[key]
        count = counts.total()
        remaining = ceil(count * 0.9)
        quantile = None
        for drop, frequency in sorted(counts.items()):
            remaining -= frequency
            if remaining <= 0:
                quantile = drop
                break
        return TrendScope(
            source=source,
            sample_count=count,
            downward_count=count - counts[0],
            p90_downward_minutes=quantile,
        )
