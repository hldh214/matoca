import asyncio
from typing import Protocol

from matoca_service.collection.models import CollectionCycle
from matoca_service.storage.models import CollectionWrite


class MerchantCycleReader(Protocol):
    async def read_collection_cycle(self, merchant_key: str) -> CollectionCycle: ...


class CycleRepository(Protocol):
    def save_cycle(self, cycle: CollectionWrite) -> None: ...


class CollectionService:
    def __init__(
        self,
        reader: MerchantCycleReader,
        repository: CycleRepository,
    ) -> None:
        self._reader = reader
        self._repository = repository

    async def collect(self, merchant_key: str) -> CollectionCycle:
        cycle = await self._reader.read_collection_cycle(merchant_key)
        await asyncio.to_thread(self._repository.save_cycle, cycle.to_storage())
        return cycle
