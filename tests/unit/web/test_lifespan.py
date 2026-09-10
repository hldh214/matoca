import asyncio

import pytest

from matoca_service.web.app import create_app
from tests.unit.web.test_app import FakeDashboardService


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
    app = create_app(FakeDashboardService(), collection_coordinator=coordinator)

    async with app.router.lifespan_context(app):
        assert coordinator.started is True

    assert coordinator.stopped is True
