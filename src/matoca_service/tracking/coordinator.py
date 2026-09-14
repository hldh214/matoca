import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol

from matoca_service.matoca.models import Waiting
from matoca_service.storage.asyncio import run_storage


class TrackingReader(Protocol):
    async def tracking_read(self, merchant_key: str) -> list[Waiting]: ...


class TrackingRepository(Protocol):
    def record_waiting(
        self, merchant: str, observed_at: datetime, waiting: list[Waiting]
    ) -> None: ...
    def record_failure(self, merchant: str, observed_at: datetime, code: str) -> None: ...


class QueueTrackingCoordinator:
    def __init__(
        self,
        merchants: Sequence[str],
        service: TrackingReader,
        repository: TrackingRepository,
        *,
        now: Callable[[], datetime] | None = None,
        reader_persists: bool = False,
        stop_timeout: float = 10,
    ) -> None:
        self._merchants = merchants
        self._service = service
        self._repository = repository
        self._now = now or (lambda: datetime.now(tz=UTC))
        self._reader_persists = reader_persists
        self._stop_timeout = stop_timeout
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self.run())

    async def run_once(self) -> None:
        for merchant in self._merchants:
            observed_at = self._now()
            try:
                waiting = await self._service.tracking_read(merchant)
            except Exception:
                with suppress(Exception):
                    await run_storage(
                        self._repository.record_failure, merchant, observed_at, "read_failed"
                    )
            else:
                # Real MatocaService persists inside the shared operation lock. Test readers
                # and simpler adapters use the repository boundary here.
                if not self._reader_persists:
                    with suppress(Exception):
                        await run_storage(
                            self._repository.record_waiting, merchant, observed_at, waiting
                        )

    async def run(self) -> None:
        try:
            while not self._stop.is_set():
                await self.run_once()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=60)
        finally:
            self._task = None

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=self._stop_timeout)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
