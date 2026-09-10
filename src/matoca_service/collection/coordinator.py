import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

from matoca_service.collection.schedule import PollSchedule
from matoca_service.config import MerchantRegistry
from matoca_service.matoca.client import MatocaApiError
from matoca_service.storage.models import MerchantPollState

BACKOFF_INTERVALS = (
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=4),
    timedelta(minutes=8),
    timedelta(minutes=15),
)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _no_active_task(merchant_key: str) -> bool:
    return False


class Collector(Protocol):
    async def collect(self, merchant_key: str) -> object: ...


class PollStateRepository(Protocol):
    def poll_state(self, merchant_key: str) -> MerchantPollState: ...

    def update_poll_state(self, state: MerchantPollState) -> MerchantPollState: ...


class CollectionRateLimited(RuntimeError):
    def __init__(self, retry_after: timedelta | None = None) -> None:
        super().__init__("collection rate limited")
        self.retry_after = retry_after


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

    def start(self) -> None:
        if self._task is None:
            self._stop_event.clear()
            self._task = asyncio.create_task(self.run())

    async def run_once(self) -> None:
        now = self._now()
        tasks: list[asyncio.Task[None]] = []
        for merchant_key in self._registry.merchants:
            if not self._is_due(merchant_key, now) or merchant_key in self._in_flight:
                continue
            task = asyncio.create_task(self._collect_merchant(merchant_key, now))
            self._in_flight[merchant_key] = task
            task.add_done_callback(self._completion_callback(merchant_key))
            tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks)

    async def run(self) -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("collection coordinator requires an asyncio task")
        self._task = task
        try:
            while not self._stop_event.is_set():
                await self.run_once()
                await self._wait_until_next_due()
        finally:
            if self._task is task:
                self._task = None

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        if task is not None and task is not asyncio.current_task():
            await task

    async def _collect_merchant(self, merchant_key: str, now: datetime) -> None:
        previous = self._repository.poll_state(merchant_key)
        self._repository.update_poll_state(replace(previous, last_attempt_at=now))
        try:
            await self._collector.collect(merchant_key)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failure_at = self._now()
            retry_after = _retry_after(error, failure_at)
            delay = (
                retry_after if retry_after is not None else _next_backoff(previous.failure_count)
            )
            retry_at = failure_at + delay
            self._repository.update_poll_state(
                MerchantPollState(
                    merchant_key=merchant_key,
                    last_attempt_at=now,
                    last_success_at=previous.last_success_at,
                    retry_at=retry_at,
                    error_code=_error_code(error),
                    failure_count=min(previous.failure_count + 1, len(BACKOFF_INTERVALS)),
                )
            )
            self._next_due[merchant_key] = retry_at
        else:
            self._repository.update_poll_state(
                MerchantPollState(
                    merchant_key=merchant_key,
                    last_attempt_at=now,
                    last_success_at=now,
                    failure_count=0,
                )
            )
            self._next_due[merchant_key] = now + self._schedule.next_interval(
                merchant_key, now, self._has_active_task(merchant_key)
            )

    def _is_due(self, merchant_key: str, now: datetime) -> bool:
        state = self._repository.poll_state(merchant_key)
        if state.retry_at is not None and state.retry_at > now:
            return False
        due_at = self._next_due.get(merchant_key)
        return due_at is None or due_at <= now

    async def _wait_until_next_due(self) -> None:
        now = self._now()
        due_at = min(
            (self._merchant_due_at(merchant_key, now) for merchant_key in self._registry.merchants),
            default=now + timedelta(minutes=1),
        )
        timeout = max(0.0, (due_at - now).total_seconds())
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)

    def _merchant_due_at(self, merchant_key: str, now: datetime) -> datetime:
        state = self._repository.poll_state(merchant_key)
        if state.retry_at is not None and state.retry_at > now:
            return state.retry_at
        return self._next_due.get(merchant_key, now)

    def _discard_completed_task(self, merchant_key: str, task: asyncio.Task[None]) -> None:
        if self._in_flight.get(merchant_key) is task:
            del self._in_flight[merchant_key]

    def _completion_callback(self, merchant_key: str) -> Callable[[asyncio.Task[None]], None]:
        def discard(task: asyncio.Task[None]) -> None:
            self._discard_completed_task(merchant_key, task)

        return discard


def _retry_after(error: Exception, now: datetime) -> timedelta | None:
    if isinstance(error, CollectionRateLimited):
        return error.retry_after
    if not isinstance(error, httpx.HTTPStatusError) or error.response.status_code != 429:
        return None
    value = error.response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = int(value)
    except ValueError:
        try:
            deadline = parsedate_to_datetime(value)
        except TypeError, ValueError:
            return None
        return max(timedelta(), deadline.astimezone(UTC) - now.astimezone(UTC))
    return timedelta(seconds=max(0, seconds))


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
