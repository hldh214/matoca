from types import SimpleNamespace
from typing import Any

import pytest

import matoca_service.automation.coordinator as coordinator_module
from matoca_service.automation.coordinator import AutomationCoordinator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_seconds", "expected_wait"),
    [
        pytest.param([25], 35, id="partial-minute-work"),
        pytest.param([10, 65], 0, id="later-task-overruns-earlier-deadline"),
    ],
)
async def test_evaluation_work_does_not_extend_minute_cadence(
    monkeypatch: pytest.MonkeyPatch, task_seconds: list[int], expected_wait: int
) -> None:
    clock = [100.0]
    task_deadlines: list[float] = []
    waits: list[float] = []

    class Runner:
        async def run_once(self) -> None:
            for duration in task_seconds:
                clock[0] += duration
                task_deadlines.append(clock[0] + 60)

    coordinator = AutomationCoordinator(Runner())  # type: ignore[arg-type]
    monkeypatch.setattr(
        coordinator_module, "time", SimpleNamespace(monotonic=lambda: clock[0]), raising=False
    )

    async def wait_for(awaitable: Any, timeout: float) -> None:
        awaitable.close()
        waits.append(timeout)
        coordinator._stopped = True

    monkeypatch.setattr(coordinator_module.asyncio, "wait_for", wait_for)
    await coordinator.run()

    assert waits == [expected_wait]
    if len(task_seconds) > 1:
        assert task_deadlines[0] < clock[0]
