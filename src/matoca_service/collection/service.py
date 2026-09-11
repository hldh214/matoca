import asyncio
import sqlite3
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
        self._pending_cycles: dict[str, CollectionWrite] = {}

    async def collect(self, merchant_key: str) -> CollectionCycle:
        pending = self._pending_cycles.get(merchant_key)
        if pending is not None:
            # Admission already honored the previous rate-limit deadline. Restore
            # unsaved evidence before allowing a newer read to replace it.
            await run_storage(self._repository.save_cycle, pending)
            del self._pending_cycles[merchant_key]
        cycle = await self._reader.read_collection_cycle(merchant_key)
        self._pending_cycles[merchant_key] = cycle.to_storage()
        try:
            await run_storage(self._repository.save_cycle, self._pending_cycles[merchant_key])
        except asyncio.CancelledError:
            if cycle.rate_limit is None:
                raise
            # Persistence has drained. Finish recording the known 429 even when
            # shutdown cancelled the await while that transaction was running.
            raise cycle.rate_limit from None
        except sqlite3.Error, OSError:
            if cycle.rate_limit is None:
                raise
            # A failed observation transaction must not replace the known
            # upstream deadline with generic storage-failure backoff.
            raise cycle.rate_limit from None
        del self._pending_cycles[merchant_key]
        if cycle.rate_limit is not None:
            raise cycle.rate_limit
        return cycle
