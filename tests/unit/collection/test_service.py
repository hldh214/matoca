from datetime import UTC, datetime

import pytest

from matoca_service.collection.models import CollectedShop, CollectionCycle
from matoca_service.collection.service import CollectionService
from matoca_service.matoca.models import Shop, Waiting
from matoca_service.storage.models import CollectionWrite

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
