import asyncio
import logging
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from matoca_service.collection.models import CollectionRateLimited as CollectionRateLimited
from matoca_service.collection.schedule import PollSchedule
from matoca_service.config import MerchantRegistry
from matoca_service.matoca.client import MatocaApiError
from matoca_service.storage.asyncio import run_storage
from matoca_service.storage.models import MerchantPollState

BACKOFF_INTERVALS = (
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=4),
    timedelta(minutes=8),
    timedelta(minutes=15),
)
TOKYO = ZoneInfo("Asia/Tokyo")
logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _no_active_task(merchant_key: str) -> bool:
    return False


class Collector(Protocol):
    async def collect(self, merchant_key: str) -> object: ...


class PollStateRepository(Protocol):
    def poll_state(self, merchant_key: str) -> MerchantPollState: ...

    def update_poll_state(self, state: MerchantPollState) -> MerchantPollState: ...

    def rollup_and_prune(self, now: datetime) -> object: ...


class CollectionCoordinator:
    def __init__(
        self,
        registry: MerchantRegistry,
        collector: Collector,
        schedule: PollSchedule,
        repository: PollStateRepository,
        *,
        now: Callable[[], datetime] = _utc_now,
        has_active_task: Callable[[str], bool] = _no_active_task,
        shutdown_timeout: float = 0.1,
        drain_timeout: float = 10.0,
    ) -> None:
        self._registry = registry
        self._collector = collector
        self._schedule = schedule
        self._repository = repository
        self._now = now
        self._has_active_task = has_active_task
        self._stop_event = asyncio.Event()
        self._in_flight: dict[str, asyncio.Task[None]] = {}
        self._next_due: dict[str, datetime] = {}
        self._task: asyncio.Task[None] | None = None
        self._last_maintenance_day: date | None = None
        self._maintenance_lock = asyncio.Lock()
        self._shutdown_timeout = shutdown_timeout
        self._drain_timeout = drain_timeout
        self._storage_retry_at: datetime | None = None
        self._completed_at: dict[str, datetime] = {}
        self._pending_poll_states: dict[str, MerchantPollState] = {}
        self._shutdown_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop_event.clear()
            self._shutdown_task = None
            self._task = asyncio.create_task(self.run())

    async def run_once(self) -> None:
        now = self._now()
        if self._stop_event.is_set() or (
            self._storage_retry_at is not None and now < self._storage_retry_at
        ):
            return
        try:
            async with self._maintenance_lock:
                await self._run_maintenance_if_due(now)
        except sqlite3.Error, OSError:
            self._storage_failed(now)
            return
        tasks: list[asyncio.Task[None]] = []
        for merchant_key in self._registry.merchants:
            if merchant_key in self._in_flight or self._stop_event.is_set():
                continue
            tasks.append(self._admit(merchant_key, force=False))
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks))

    async def collect(self, merchant_key: str, *, requested_at: datetime | None = None) -> None:
        if self._stop_event.is_set():
            return
        if (
            requested_at is not None
            and self._completed_at.get(merchant_key, datetime.min.replace(tzinfo=UTC))
            >= requested_at
        ):
            return
        task = self._in_flight.get(merchant_key)
        if task is None:
            task = self._admit(merchant_key, force=True)
        # HTTP cancellation must not abandon the shared collection or its write.
        await asyncio.shield(task)

    def _admit(self, merchant_key: str, *, force: bool) -> asyncio.Task[None]:
        task = asyncio.create_task(self._collect_merchant(merchant_key, self._now(), force=force))
        self._in_flight[merchant_key] = task
        task.add_done_callback(self._completion_callback(merchant_key))
        return task

    async def run(self) -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("collection coordinator requires an asyncio task")
        self._task = task
        try:
            while not self._stop_event.is_set():
                try:
                    await self.run_once()
                    await self._wait_until_next_due()
                except sqlite3.Error, OSError:
                    self._storage_failed(self._now())
                    await self._wait(60.0)
        finally:
            if self._task is task:
                self._task = None

    async def stop(self) -> None:
        self._stop_event.set()
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._stop_and_drain())
        cancelled = False
        while not self._shutdown_task.done():
            try:
                await asyncio.shield(self._shutdown_task)
            except asyncio.CancelledError:
                cancelled = True
        self._shutdown_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _stop_and_drain(self) -> None:
        tasks = set(self._in_flight.values())
        if self._task is not None and self._task is not asyncio.current_task():
            tasks.add(self._task)
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=self._shutdown_timeout)
        for task in pending:
            task.cancel()
        if pending:
            _, undrained = await asyncio.wait(pending, timeout=self._drain_timeout)
            if undrained:
                raise RuntimeError("collection shutdown could not drain storage")
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _collect_merchant(
        self, merchant_key: str, now: datetime, *, force: bool = False
    ) -> None:
        try:
            if self._storage_retry_at is not None and now < self._storage_retry_at:
                return
            pending_state = self._pending_poll_states.get(merchant_key)
            if pending_state is not None:
                await self._save_poll_state(pending_state)
            previous = await run_storage(self._repository.poll_state, merchant_key)
            if previous.retry_at is not None and previous.retry_at > now:
                self._next_due[merchant_key] = previous.retry_at
                return
            if not force and self._next_due.get(merchant_key, now) > now:
                return
            await run_storage(
                self._repository.update_poll_state, replace(previous, last_attempt_at=now)
            )
            try:
                await self._collector.collect(merchant_key)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failure_at = self._now()
                retry_after = _retry_after(error, failure_at)
                delay = (
                    retry_after
                    if retry_after is not None
                    else _next_backoff(previous.failure_count)
                )
                retry_at = failure_at + delay
                # Keep the in-memory deadline even if the durable write fails.
                self._next_due[merchant_key] = retry_at
                await self._save_poll_state(
                    MerchantPollState(
                        merchant_key=merchant_key,
                        last_attempt_at=now,
                        last_success_at=previous.last_success_at,
                        retry_at=retry_at,
                        error_code=_error_code(error),
                        failure_count=min(previous.failure_count + 1, len(BACKOFF_INTERVALS)),
                    ),
                )
            else:
                await self._save_poll_state(
                    MerchantPollState(
                        merchant_key=merchant_key,
                        last_attempt_at=now,
                        last_success_at=now,
                        failure_count=0,
                    ),
                )
                self._next_due[merchant_key] = self._now() + await run_storage(
                    self._schedule.next_interval,
                    merchant_key,
                    self._now(),
                    self._has_active_task(merchant_key),
                )
            self._completed_at[merchant_key] = self._now()
        except sqlite3.Error, OSError:
            self._storage_failed(self._now())

    def _storage_failed(self, now: datetime) -> None:
        self._storage_retry_at = now + timedelta(minutes=1)
        logger.warning("collection storage unavailable; retrying in 60 seconds")

    async def _save_poll_state(self, state: MerchantPollState) -> None:
        self._pending_poll_states[state.merchant_key] = state
        await run_storage(self._repository.update_poll_state, state)
        self._pending_poll_states.pop(state.merchant_key, None)

    async def _run_maintenance_if_due(self, now: datetime) -> None:
        local_day = now.astimezone(TOKYO).date()
        if self._last_maintenance_day == local_day:
            return
        await run_storage(self._repository.rollup_and_prune, now)
        self._last_maintenance_day = local_day

    async def _wait_until_next_due(self) -> None:
        now = self._now()
        due_at = min(self._next_due.values(), default=now + timedelta(minutes=1))
        if self._pending_poll_states and self._storage_retry_at is not None:
            due_at = min(due_at, self._storage_retry_at)
        if self._storage_retry_at is not None:
            due_at = max(due_at, self._storage_retry_at)
        await self._wait(max(0.01, (due_at - now).total_seconds()))

    async def _wait(self, timeout: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)

    def _discard_completed_task(self, merchant_key: str, task: asyncio.Task[None]) -> None:
        if self._in_flight.get(merchant_key) is task:
            del self._in_flight[merchant_key]

    def _completion_callback(self, merchant_key: str) -> Callable[[asyncio.Task[None]], None]:
        def discard(task: asyncio.Task[None]) -> None:
            self._discard_completed_task(merchant_key, task)

        return discard


def _retry_after(error: Exception, now: datetime) -> timedelta | None:
    if isinstance(error, CollectionRateLimited):
        if error.retry_at is not None:
            return max(timedelta(), error.retry_at - now)
        return error.retry_after
    if not isinstance(error, httpx.HTTPStatusError) or error.response.status_code != 429:
        return None
    return _retry_after(CollectionRateLimited.from_response(error.response, now), now)


def _next_backoff(failure_count: int) -> timedelta:
    return BACKOFF_INTERVALS[min(failure_count, len(BACKOFF_INTERVALS) - 1)]


def _error_code(error: Exception) -> str:
    if isinstance(error, CollectionRateLimited):
        return "rate_limited"
    if isinstance(error, httpx.HTTPStatusError):
        return (
            "rate_limited"
            if error.response.status_code == 429
            else f"http_{error.response.status_code}"
        )
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.RequestError):
        return "transport"
    if isinstance(error, MatocaApiError | ValueError):
        return "malformed_response"
    return "collection_failed"
