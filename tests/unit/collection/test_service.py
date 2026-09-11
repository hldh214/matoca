import asyncio
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from matoca_service.collection.coordinator import CollectionCoordinator
from matoca_service.collection.models import CollectedShop, CollectionCycle, CollectionRateLimited
from matoca_service.collection.schedule import PollSchedule
from matoca_service.collection.service import CollectionService
from matoca_service.config import MerchantRegistry
from matoca_service.matoca.models import Shop, Waiting
from matoca_service.storage.database import Database
from matoca_service.storage.models import CollectionWrite
from matoca_service.storage.repositories import ShopRepository

FIXED_NOW = datetime(2026, 9, 10, 8, 1, tzinfo=UTC)


class FakeMerchantReader:
    def __init__(self, cycle: CollectionCycle) -> None:
        self.cycle = cycle
        self.merchant_keys: list[str] = []

    async def read_collection_cycle(self, merchant_key: str) -> CollectionCycle:
        self.merchant_keys.append(merchant_key)
        return self.cycle


class RecordingShopRepository:
    def __init__(self) -> None:
        self.saved: list[CollectionWrite] = []

    def save_cycle(self, cycle: CollectionWrite) -> None:
        self.saved.append(cycle)


@pytest.mark.asyncio
async def test_collect_persists_the_complete_cycle() -> None:
    cycle = CollectionCycle(
        merchant_key="sawayaka",
        observed_at=FIXED_NOW,
        shops=[
            CollectedShop(
                shop=Shop(id=1, name="A", current_waiting=10, is_open=True, is_issuable=True),
                list_fresh=True,
                detail_fresh=True,
            ),
            CollectedShop(
                shop=Shop(id=2, name="B", current_waiting=4, is_open=True, is_issuable=True),
                list_fresh=True,
                detail_fresh=True,
            ),
        ],
        waiting=[Waiting(id=31)],
    )
    reader = FakeMerchantReader(cycle)
    repository = RecordingShopRepository()

    result = await CollectionService(reader, repository).collect("sawayaka")

    assert result is cycle
    assert reader.merchant_keys == ["sawayaka"]
    assert repository.saved == [cycle.to_storage()]


def test_cycle_storage_preserves_failed_detail_as_unobserved() -> None:
    cycle = CollectionCycle(
        merchant_key="sawayaka",
        observed_at=FIXED_NOW,
        shops=[
            CollectedShop(
                shop=Shop(id=1, name="A", current_waiting=10),
                list_fresh=True,
                detail_fresh=False,
                error_code="timeout",
            )
        ],
        waiting=[],
    )

    observation = cycle.to_storage().shops[0]

    assert observation.shop.current_waiting == 10
    assert observation.detail_fresh is False
    assert observation.waiting_minutes is None
    assert observation.is_open is None
    assert observation.error_code == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_stop", [False, True])
async def test_shutdown_during_rate_limited_persistence_drains_and_keeps_backoff(
    tmp_path: Path,
    cancel_stop: bool,
) -> None:
    database = Database(tmp_path / "data" / "matoca.db")
    database.initialize()
    entered = threading.Event()
    release = threading.Event()

    class DelayedRepository(ShopRepository):
        def save_cycle(self, cycle: CollectionWrite) -> None:
            entered.set()
            release.wait(2)
            super().save_cycle(cycle)

    repository = DelayedRepository(database)
    cycle = CollectionCycle(
        "sawayaka",
        FIXED_NOW,
        [CollectedShop(Shop(id=1, name="Partial", current_waiting=8), True, False, "rate_limited")],
        [],
        rate_limit=CollectionRateLimited(retry_after=timedelta(minutes=3)),
    )
    registry = MerchantRegistry.model_validate(
        {"merchants": {"sawayaka": MerchantRegistry.load_builtin().merchants["sawayaka"]}}
    )
    coordinator = CollectionCoordinator(
        registry,
        CollectionService(FakeMerchantReader(cycle), repository),
        PollSchedule(repository),
        repository,
        now=lambda: FIXED_NOW,
    )
    coordinator.start()
    try:
        while not entered.is_set():
            await asyncio.sleep(0.001)
        stopping = asyncio.create_task(coordinator.stop())
        await asyncio.sleep(0.15)
        assert not stopping.done(), "shutdown must keep ownership of the unfinished transaction"
        if cancel_stop:
            stopping.cancel()
            await asyncio.sleep(0.01)
            stopping.cancel()
            await asyncio.sleep(0.01)
            assert not stopping.done(), "cancelling shutdown must not abandon its workers"
        release.set()
        if cancel_stop:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(stopping, timeout=1)
        else:
            await asyncio.wait_for(stopping, timeout=1)
        assert repository.latest("sawayaka")[0].observation.current_waiting == 8
        state = repository.poll_state("sawayaka")
        assert state.error_code == "rate_limited"
        assert state.retry_at == FIXED_NOW + timedelta(minutes=3)
        assert coordinator._in_flight == {}
    finally:
        release.set()
        await coordinator.stop()
