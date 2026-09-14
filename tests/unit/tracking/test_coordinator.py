import asyncio
from datetime import UTC, datetime

import pytest

from matoca_service.matoca.models import Waiting
from matoca_service.tracking.coordinator import QueueTrackingCoordinator


class FakeService:
    def __init__(self) -> None:
        self.reads: dict[str, object] = {
            "one": [Waiting(id=1, shop_id=10, count=2)],
            "two": RuntimeError("offline"),
        }

    async def tracking_read(self, merchant_key: str) -> list[Waiting]:
        value = self.reads[merchant_key]
        if isinstance(value, Exception):
            raise value
        return value  # type: ignore[return-value]


class RecordingRepository:
    def __init__(self) -> None:
        self.successes: list[tuple[str, list[Waiting]]] = []
        self.failures: list[tuple[str, str]] = []

    def record_waiting(self, merchant: str, observed_at: datetime, waiting: list[Waiting]) -> None:
        del observed_at
        self.successes.append((merchant, waiting))

    def record_failure(self, merchant: str, observed_at: datetime, code: str) -> None:
        del observed_at
        self.failures.append((merchant, code))


@pytest.mark.asyncio
async def test_one_failed_merchant_does_not_hide_another_merchants_queue() -> None:
    repo = RecordingRepository()
    coordinator = QueueTrackingCoordinator(
        ["one", "two"], FakeService(), repo, now=lambda: datetime(2026, 9, 13, tzinfo=UTC)
    )

    await coordinator.run_once()

    assert repo.successes == [("one", [Waiting(id=1, shop_id=10, count=2)])]
    assert repo.failures == [("two", "read_failed")]


@pytest.mark.asyncio
async def test_stop_cancels_a_stuck_cycle_with_bounded_shutdown() -> None:
    class StuckService:
        async def tracking_read(self, merchant_key: str) -> list[Waiting]:
            del merchant_key
            await asyncio.Event().wait()
            return []

    coordinator = QueueTrackingCoordinator(
        ["one"], StuckService(), RecordingRepository(), stop_timeout=0.01
    )
    coordinator.start()
    await asyncio.sleep(0)

    await asyncio.wait_for(coordinator.stop(), timeout=1)
