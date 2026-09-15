import asyncio
import logging
from contextlib import suppress

from matoca_service.automation.runner import AutomationRunner

logger = logging.getLogger(__name__)


class AutomationCoordinator:
    def __init__(self, runner: AutomationRunner) -> None:
        self.runner = runner
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopped = False

    def start(self) -> None:
        if self._task is None:
            self._stopped = False
            self._task = asyncio.create_task(self.run())

    def wake(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        while not self._stopped:
            self._wake.clear()
            try:
                await self.runner.run_once()
            except Exception:
                logger.warning("automation evaluation unavailable; retrying in 60 seconds")
            if not self._stopped:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), 60)

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
