import asyncio
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from matoca_service.collection.coordinator import CollectionCoordinator, CollectionRateLimited
from matoca_service.collection.schedule import PollSchedule
from matoca_service.config import MerchantRegistry
from matoca_service.storage.models import MerchantPollState, PollWindow

NOW = datetime(2026, 9, 10, 8, tzinfo=UTC)


class MemoryRepository:
    def __init__(self, window: PollWindow | None = None) -> None:
        self.window = window
        self.states: dict[str, MerchantPollState] = {}
        self.maintenance_calls: list[datetime] = []

    def poll_window(self, merchant_key: str, now: datetime) -> PollWindow | None:
        return self.window

    def poll_state(self, merchant_key: str) -> MerchantPollState:
        return self.states.get(merchant_key, MerchantPollState(merchant_key))

    def update_poll_state(self, state: MerchantPollState) -> MerchantPollState:
        self.states[state.merchant_key] = state
        return state

    def rollup_and_prune(self, now: datetime) -> None:
        self.maintenance_calls.append(now)


class BlockingCollector:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def collect(self, merchant_key: str) -> None:
        self.calls.append(merchant_key)
        self.started.set()
        await self.release.wait()


class FailingCollector:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def collect(self, merchant_key: str) -> None:
        raise self.error


class SlowFailingCollector:
    def __init__(self, advance: Callable[[], None], error: Exception) -> None:
        self._advance = advance
        self._error = error

    async def collect(self, merchant_key: str) -> None:
        self._advance()
        raise self._error


class SuccessfulCollector:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def collect(self, merchant_key: str) -> None:
        self.started.set()
        return None


class ScriptedCollector:
    def __init__(self, outcomes: list[Exception | None]) -> None:
        self.outcomes = outcomes

    async def collect(self, merchant_key: str) -> None:
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise outcome


def registry() -> MerchantRegistry:
    return MerchantRegistry.model_validate(
        {
            "merchants": {
                "sawayaka": {
                    "name": "Synthetic Merchant",
                    "liff_id": "synthetic",
                    "api_base_url": "https://api.example.test",
                    "origin": "https://example.test",
                    "entry_url": "https://example.test/entry",
                    "line_entry_url": "https://line.example.test/entry",
                }
            }
        }
    )


def coordinator_for(
    collector: object,
    repository: MemoryRepository,
    now: Callable[[], datetime] = lambda: NOW,
) -> CollectionCoordinator:
    return CollectionCoordinator(
        registry(), collector, PollSchedule(repository), repository, now=now
    )


@pytest.mark.asyncio
async def test_run_once_does_not_start_second_cycle_for_busy_merchant() -> None:
    collector = BlockingCollector()
    repository = MemoryRepository()
    coordinator = coordinator_for(collector, repository)
    first = asyncio.create_task(coordinator.run_once())
    await collector.started.wait()

    await coordinator.run_once()

    assert collector.calls == ["sawayaka"]
    collector.release.set()
    await first


@pytest.mark.asyncio
async def test_stop_cancels_and_drains_a_blocked_collection() -> None:
    collector = BlockingCollector()
    coordinator = coordinator_for(collector, MemoryRepository())
    coordinator.start()
    await collector.started.wait()
    try:
        await asyncio.wait_for(coordinator.stop(), timeout=0.3)
        assert not coordinator._in_flight
        assert coordinator._task is None
    finally:
        collector.release.set()
        if coordinator._task is not None:
            await coordinator.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["poll_state", "update_poll_state", "rollup_and_prune", "poll_window"]
)
async def test_storage_failure_keeps_background_loop_alive(
    stage: str, caplog: pytest.LogCaptureFixture
) -> None:
    repository = MemoryRepository()
    original = getattr(repository, stage)
    failed = threading.Event()

    def fail_once(*args: object) -> object:
        if not failed.is_set():
            failed.set()
            raise sqlite3.OperationalError("synthetic secret storage error")
        return original(*args)

    setattr(repository, stage, fail_once)
    collector = SuccessfulCollector()
    current = NOW
    coordinator = coordinator_for(collector, repository, now=lambda: current)
    coordinator.start()
    try:
        async with asyncio.timeout(1):
            while not failed.is_set():
                await asyncio.sleep(0.001)
        await asyncio.sleep(0.05)
        assert coordinator._task is not None and not coordinator._task.done()
        assert "synthetic secret" not in caplog.text
        current += timedelta(minutes=1)
        await coordinator.run_once()
        assert repository.poll_state("sawayaka").last_success_at is not None
    finally:
        await coordinator.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["poll_state", "update_poll_state", "rollup_and_prune", "poll_window"]
)
async def test_slow_storage_leaves_event_loop_responsive(stage: str) -> None:
    repository = MemoryRepository()
    original = getattr(repository, stage)
    entered = threading.Event()
    release = threading.Event()

    def delayed(*args: object) -> object:
        entered.set()
        release.wait(0.4)
        return original(*args)

    setattr(repository, stage, delayed)
    coordinator = coordinator_for(SuccessfulCollector(), repository)
    running = asyncio.create_task(coordinator.run_once())
    try:
        started = asyncio.get_running_loop().time()
        while not entered.is_set():
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.01)
        assert asyncio.get_running_loop().time() - started < 0.2
    finally:
        release.set()
        await running


@pytest.mark.asyncio
async def test_force_preserves_backoff_after_transient_state_write_failure() -> None:
    repository = MemoryRepository()
    original = repository.update_poll_state
    failed = False

    def fail_backoff_once(state: MerchantPollState) -> MerchantPollState:
        nonlocal failed
        if state.retry_at is not None and not failed:
            failed = True
            raise sqlite3.OperationalError("synthetic failure")
        return original(state)

    repository.update_poll_state = fail_backoff_once
    collector = ScriptedCollector([CollectionRateLimited(timedelta(minutes=3)), None])
    current = NOW
    coordinator = coordinator_for(collector, repository, now=lambda: current)
    await coordinator.run_once()
    current += timedelta(minutes=1)
    await coordinator.collect("sawayaka")
    assert repository.poll_state("sawayaka").retry_at == NOW + timedelta(minutes=3)
    assert repository.poll_state("sawayaka").last_success_at is None


@pytest.mark.asyncio
async def test_run_once_runs_retention_once_per_tokyo_day() -> None:
    repository = MemoryRepository()
    collector = SuccessfulCollector()
    current = NOW

    def now() -> datetime:
        return current

    coordinator = coordinator_for(collector, repository, now)
    await coordinator.run_once()
    current += timedelta(minutes=5)
    await coordinator.run_once()
    current += timedelta(days=1)
    await coordinator.run_once()

    assert repository.maintenance_calls == [NOW, NOW + timedelta(days=1, minutes=5)]


@pytest.mark.asyncio
async def test_stop_wakes_the_run_loop() -> None:
    collector = SuccessfulCollector()
    coordinator = coordinator_for(collector, MemoryRepository())
    run_task = asyncio.create_task(coordinator.run())
    await collector.started.wait()

    await coordinator.stop()

    assert run_task.done()


@pytest.mark.asyncio
async def test_start_runs_and_stop_stops_the_collection_loop() -> None:
    collector = SuccessfulCollector()
    coordinator = coordinator_for(collector, MemoryRepository())

    coordinator.start()
    await collector.started.wait()
    await coordinator.stop()

    assert coordinator._task is None


@pytest.mark.asyncio
async def test_429_sets_merchant_retry_deadline() -> None:
    repository = MemoryRepository()
    collector = FailingCollector(CollectionRateLimited(retry_after=timedelta(minutes=3)))

    await coordinator_for(collector, repository).run_once()

    state = repository.poll_state("sawayaka")
    assert state.last_attempt_at == NOW
    assert state.retry_at == NOW + timedelta(minutes=3)
    assert state.error_code == "rate_limited"
    assert state.failure_count == 1


@pytest.mark.asyncio
async def test_slow_429_calculates_retry_deadline_from_failure_time() -> None:
    repository = MemoryRepository()
    current = NOW

    def now() -> datetime:
        return current

    def advance() -> None:
        nonlocal current
        current += timedelta(minutes=2)

    collector = SlowFailingCollector(
        advance, CollectionRateLimited(retry_after=timedelta(minutes=1))
    )

    await coordinator_for(collector, repository, now).run_once()

    state = repository.poll_state("sawayaka")
    assert state.last_attempt_at == NOW
    assert state.retry_at == NOW + timedelta(minutes=3)


@pytest.mark.asyncio
async def test_http_429_honors_retry_after_header() -> None:
    repository = MemoryRepository()
    request = httpx.Request("GET", "https://api.example.test/shops")
    response = httpx.Response(429, headers={"Retry-After": "180"}, request=request)
    collector = FailingCollector(
        httpx.HTTPStatusError("rate limited", request=request, response=response)
    )

    await coordinator_for(collector, repository).run_once()

    assert repository.poll_state("sawayaka").retry_at == NOW + timedelta(minutes=3)


@pytest.mark.asyncio
async def test_http_429_honors_zero_second_retry_after_header() -> None:
    repository = MemoryRepository()
    request = httpx.Request("GET", "https://api.example.test/shops")
    response = httpx.Response(429, headers={"Retry-After": "0"}, request=request)
    collector = FailingCollector(
        httpx.HTTPStatusError("rate limited", request=request, response=response)
    )

    await coordinator_for(collector, repository).run_once()

    assert repository.poll_state("sawayaka").retry_at == NOW


@pytest.mark.asyncio
async def test_missing_retry_after_progresses_after_retry_after_failure() -> None:
    repository = MemoryRepository()
    current = NOW

    def now() -> datetime:
        return current

    coordinator = coordinator_for(
        ScriptedCollector([CollectionRateLimited(timedelta(minutes=3)), CollectionRateLimited()]),
        repository,
        now,
    )
    await coordinator.run_once()
    current += timedelta(minutes=3)
    await coordinator.run_once()

    state = repository.poll_state("sawayaka")
    assert state.failure_count == 2
    assert state.retry_at == current + timedelta(minutes=2)


@pytest.mark.asyncio
async def test_missing_retry_after_uses_bounded_exponential_backoff() -> None:
    repository = MemoryRepository()
    current = NOW

    def now() -> datetime:
        return current

    coordinator = coordinator_for(FailingCollector(CollectionRateLimited()), repository, now)
    deadlines: list[datetime | None] = []
    for failure_count, expected_delay in enumerate((1, 2, 4, 8, 15), start=1):
        await coordinator.run_once()
        deadline = repository.poll_state("sawayaka").retry_at
        deadlines.append(deadline)
        assert deadline == current + timedelta(minutes=expected_delay)
        assert deadline is not None
        assert repository.poll_state("sawayaka").failure_count == failure_count
        current = deadline

    assert deadlines == [
        NOW + timedelta(minutes=1),
        NOW + timedelta(minutes=3),
        NOW + timedelta(minutes=7),
        NOW + timedelta(minutes=15),
        NOW + timedelta(minutes=30),
    ]


@pytest.mark.asyncio
async def test_slow_failures_progress_through_backoff_rungs() -> None:
    repository = MemoryRepository()
    current = NOW

    def now() -> datetime:
        return current

    def advance() -> None:
        nonlocal current
        current += timedelta(minutes=2)

    coordinator = coordinator_for(
        SlowFailingCollector(advance, CollectionRateLimited()), repository, now
    )
    deadlines: list[datetime] = []
    for expected_count in range(1, 6):
        await coordinator.run_once()
        state = repository.poll_state("sawayaka")
        assert state.failure_count == expected_count
        assert state.retry_at is not None
        deadlines.append(state.retry_at)
        current = state.retry_at

    assert deadlines == [
        NOW + timedelta(minutes=3),
        NOW + timedelta(minutes=7),
        NOW + timedelta(minutes=13),
        NOW + timedelta(minutes=23),
        NOW + timedelta(minutes=40),
    ]


@pytest.mark.asyncio
async def test_success_resets_retry_deadline_and_fallback_sequence() -> None:
    repository = MemoryRepository()
    current = NOW

    def now() -> datetime:
        return current

    coordinator = coordinator_for(
        ScriptedCollector(
            [CollectionRateLimited(), CollectionRateLimited(), None, CollectionRateLimited()]
        ),
        repository,
        now,
    )
    await coordinator.run_once()
    current += timedelta(minutes=1)
    await coordinator.run_once()

    current += timedelta(minutes=2)
    await coordinator.run_once()
    succeeded = repository.poll_state("sawayaka")
    assert succeeded.last_success_at == current
    assert succeeded.retry_at is None
    assert succeeded.error_code is None
    assert succeeded.failure_count == 0

    current += timedelta(minutes=5)
    await coordinator.run_once()
    assert repository.poll_state("sawayaka").retry_at == current + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_failure_persists_stable_error_code_without_exception_text() -> None:
    repository = MemoryRepository()
    collector = FailingCollector(RuntimeError("response payload: secret"))

    await coordinator_for(collector, repository).run_once()

    assert repository.poll_state("sawayaka").error_code == "collection_failed"
