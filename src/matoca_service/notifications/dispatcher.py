import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

from matoca_service.notifications.repository import NotificationRepository
from matoca_service.notifications.sender import PushSender
from matoca_service.storage.asyncio import run_storage

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    def __init__(self, repository: NotificationRepository, sender: PushSender) -> None:
        self.repository = repository
        self.sender = sender
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            await self._task
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def dispatch_once(self) -> None:
        deliveries = await run_storage(self.repository.pending, datetime.now(UTC))
        for delivery in deliveries:
            if self._stopping:
                break
            status = await run_storage(self.sender.send, delivery)
            await run_storage(self.repository.result, delivery, status, datetime.now(UTC))

    async def _run(self) -> None:
        while not self._stopping:
            self._wake.clear()
            try:
                await self.dispatch_once()
            except Exception:
                logger.warning("通知処理を次回再試行します")
            if self._stopping:
                break
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=15)
