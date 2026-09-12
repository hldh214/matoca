import asyncio
from typing import cast

import pytest

from matoca_service.web.app import DashboardService, create_app


class RecordingCoordinator:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        await asyncio.sleep(0)
        self.stopped = True


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_collection_coordinator() -> None:
    coordinator = RecordingCoordinator()
    unused_service = cast(DashboardService, object())
    app = create_app(unused_service, collection_coordinator=coordinator)

    async with app.router.lifespan_context(app):
        assert coordinator.started is True

    assert coordinator.stopped is True
