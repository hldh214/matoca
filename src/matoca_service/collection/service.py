import asyncio
from typing import Protocol

from matoca_service.collection.models import CollectionCycle
from matoca_service.storage.asyncio import run_storage
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
        try:
            await run_storage(self._repository.save_cycle, cycle.to_storage())
        except asyncio.CancelledError:
            if cycle.rate_limit is None:
                raise
            # Persistence has drained. Finish recording the known 429 even when
            # shutdown cancelled the await while that transaction was running.
            raise cycle.rate_limit from None
        if cycle.rate_limit is not None:
            raise cycle.rate_limit
        return cycle
