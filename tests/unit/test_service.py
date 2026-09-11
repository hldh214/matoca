import asyncio
import threading
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from matoca_service.collection.coordinator import CollectionCoordinator
from matoca_service.collection.models import CollectedShop, CollectionCycle
from matoca_service.collection.schedule import PollSchedule
from matoca_service.collection.service import CollectionService
from matoca_service.config import MerchantRegistry
from matoca_service.line.models import LiffToken
from matoca_service.matoca.client import MatocaApiError, MatocaClient, ShopCatalog
from matoca_service.matoca.models import Shop, ShopForms, Waiting
from matoca_service.service import (
    DashboardData,
    MatocaService,
    QueueSubmission,
    QueueUnavailableError,
    validate_queue_submission,
)
from matoca_service.state.models import AppState, LiffTokenState, LineState
from matoca_service.state.store import JsonStateStore
from matoca_service.storage.database import Database
from matoca_service.storage.models import (
    CollectionWrite,
    MerchantPollState,
    PollWindow,
    ShopObservation,
)
from matoca_service.storage.repositories import ShopRepository

type JwtFactory = Callable[[dict[str, Any]], str]


@pytest.fixture
def stored_service(tmp_path: Path) -> tuple[MatocaService, list[str]]:
    line_client_path = tmp_path / "line_client.toml"
    line_client_path.write_text(
        "\n".join(
            [
                'host = "legy-jp.line-apps.com"',
                'application = "synthetic-app"',
                'locale = "ja_JP"',
                'protocol_version = "1"',
                'user_agent = "synthetic-agent"',
            ]
        ),
        encoding="utf-8",
    )
    database = Database(tmp_path / "data" / "matoca.db")
    database.initialize()
    observed_at = datetime(2026, 9, 10, 8, tzinfo=UTC)
    ShopRepository(database).save_cycle(
        CollectionWrite(
            merchant_key="sawayaka",
            observed_at=observed_at,
            shops=[
                ShopObservation(
                    shop=Shop(
                        id=3272,
                        name="Synthetic Shop",
                        lat=34.7042983,
                        lng=137.7344733,
                        current_waiting=12,
                        is_open=True,
                        is_issuable=True,
                    ),
                    list_fresh=True,
                    detail_fresh=True,
                )
            ],
        )
    )
    return MatocaService(line_client_path, tmp_path / "state.json", database.path), []


@pytest.mark.asyncio
async def test_merchant_snapshot_uses_database_without_upstream_request(
    stored_service: tuple[MatocaService, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, upstream_calls = stored_service

    async def unexpected_collection(merchant_key: str) -> CollectionCycle:
        upstream_calls.append(merchant_key)
        raise AssertionError("stored snapshot must not collect upstream data")

    monkeypatch.setattr(service, "read_collection_cycle", unexpected_collection)

    snapshot = await service.merchant_snapshot("sawayaka")

    assert snapshot.shops[0].id == 3272
    assert snapshot.refreshed_at == datetime(2026, 9, 10, 8, tzinfo=UTC)
    assert upstream_calls == []


@pytest.mark.asyncio
async def test_shop_detail_uses_stored_shop_coordinates(
    stored_service: tuple[MatocaService, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = stored_service
    client = FakeCycleClient(
        [],
        {3272: Shop(id=3272, name="Detail", is_open=True, is_issuable=True)},
    )
    client.events.append("list")

    async def authenticated_read(merchant_key: str, operation: Any) -> Any:
        assert merchant_key == "sawayaka"
        return await operation(client)

    monkeypatch.setattr(service, "_authenticated_read", authenticated_read)

    await service.shop_detail("sawayaka", 3272)

    assert client.detail_locations[3272] == ("34.7042983", "137.7344733")


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [True, False])
async def test_snapshot_collection_honors_durable_backoff(
    stored_service: tuple[MatocaService, list[str]], monkeypatch: pytest.MonkeyPatch, force: bool
) -> None:
    service, calls = stored_service
    service._shops.update_poll_state(
        MerchantPollState(
            "sawayaka",
            retry_at=datetime.now(tz=UTC) + timedelta(hours=1),
            error_code="rate_limited",
        )
    )
    if not force:
        service._database.write(
            lambda connection: connection.execute("DELETE FROM catalog_members")
        )

    async def unexpected(key: str) -> CollectionCycle:
        calls.append(key)
        raise AssertionError("backoff bypassed")

    monkeypatch.setattr(service, "read_collection_cycle", unexpected)
    await service.merchant_snapshot("sawayaka", force_catalog=force)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [True, False])
async def test_concurrent_snapshot_requests_collect_only_once(
    stored_service: tuple[MatocaService, list[str]], monkeypatch: pytest.MonkeyPatch, force: bool
) -> None:
    service, calls = stored_service
    if not force:
        service._database.write(
            lambda connection: connection.execute("DELETE FROM merchant_catalog_state")
        )
        service._database.write(
            lambda connection: connection.execute("DELETE FROM catalog_members")
        )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def collect(key: str) -> CollectionCycle:
        calls.append(key)
        entered.set()
        await release.wait()
        return CollectionCycle(
            key, datetime.now(tz=UTC), [CollectedShop(Shop(id=1, name="Fresh"), True, True)], []
        )

    monkeypatch.setattr(service, "read_collection_cycle", collect)
    requests = [
        asyncio.create_task(service.merchant_snapshot("sawayaka", force_catalog=force))
        for _ in range(8)
    ]
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.sleep(0.05)
    finally:
        release.set()
        snapshots = await asyncio.gather(*requests)
    assert calls == ["sawayaka"]
    assert all(snapshot.shops[0].id == 1 for snapshot in snapshots)


@pytest.mark.asyncio
async def test_empty_complete_catalog_is_cached(
    stored_service: tuple[MatocaService, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, calls = stored_service

    async def collect(key: str) -> CollectionCycle:
        calls.append(key)
        return CollectionCycle(key, datetime.now(tz=UTC), [], [])

    monkeypatch.setattr(service, "read_collection_cycle", collect)
    first = await service.merchant_snapshot("sawayaka", force_catalog=True)
    second = await service.merchant_snapshot("sawayaka")
    assert first.shops == second.shops == []
    assert calls == ["sawayaka"]


@pytest.mark.asyncio
async def test_snapshot_poll_window_does_not_block_loop(
    stored_service: tuple[MatocaService, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = stored_service
    entered, release = threading.Event(), threading.Event()

    def slow_window(key: str, now: datetime) -> None:
        entered.set()
        release.wait(0.4)

    monkeypatch.setattr(service._shops, "poll_window", slow_window)
    task = asyncio.create_task(service.merchant_snapshot("sawayaka"))
    started = asyncio.get_running_loop().time()
    try:
        while not entered.is_set():
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.01)
        assert asyncio.get_running_loop().time() - started < 0.2
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_cancelled_snapshot_request_does_not_release_shared_admission(
    stored_service: tuple[MatocaService, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, calls = stored_service
    entered, release = asyncio.Event(), asyncio.Event()

    async def collect(key: str) -> CollectionCycle:
        calls.append(key)
        entered.set()
        await release.wait()
        return CollectionCycle(
            key, datetime.now(tz=UTC), [CollectedShop(Shop(id=1, name="Shared"), True, True)], []
        )

    monkeypatch.setattr(service, "read_collection_cycle", collect)
    request = asyncio.create_task(service.merchant_snapshot("sawayaka", force_catalog=True))
    await entered.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    second = asyncio.create_task(service.merchant_snapshot("sawayaka", force_catalog=True))
    await asyncio.sleep(0.01)
    release.set()
    assert (await second).shops[0].id == 1
    assert calls == ["sawayaka"]


@pytest.mark.asyncio
async def test_forced_merchant_snapshot_collects_then_reads_the_database(
    stored_service: tuple[MatocaService, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, upstream_calls = stored_service
    observed_at = datetime(2026, 9, 10, 8, 1, tzinfo=UTC)

    async def collect(merchant_key: str) -> CollectionCycle:
        upstream_calls.append(merchant_key)
        return CollectionCycle(
            merchant_key=merchant_key,
            observed_at=observed_at,
            shops=[
                CollectedShop(
                    shop=Shop(id=3272, name="Synthetic Shop", current_waiting=9),
                    list_fresh=True,
                    detail_fresh=True,
                )
            ],
            waiting=[],
        )

    monkeypatch.setattr(service, "read_collection_cycle", collect)

    snapshot = await service.merchant_snapshot("sawayaka", force_catalog=True)

    assert snapshot.refreshed_at == observed_at
    assert snapshot.shops[0].current_waiting == 9
    assert upstream_calls == ["sawayaka"]


@pytest.mark.asyncio
async def test_merchant_snapshot_marks_partial_latest_cycle_stale(
    stored_service: tuple[MatocaService, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = stored_service
    observed_at = datetime(2026, 9, 10, 8, 1, tzinfo=UTC)

    async def partial_collection(merchant_key: str) -> CollectionCycle:
        return CollectionCycle(
            merchant_key=merchant_key,
            observed_at=observed_at,
            shops=[
                CollectedShop(
                    shop=Shop(id=3272, name="Synthetic Shop", current_waiting=12),
                    list_fresh=True,
                    detail_fresh=False,
                    error_code="timeout",
                )
            ],
            waiting=[],
        )

    monkeypatch.setattr(service, "read_collection_cycle", partial_collection)

    snapshot = await service.merchant_snapshot("sawayaka", force_catalog=True)

    assert snapshot.stale is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("now", "window", "age", "expected_stale"),
    [
        (datetime(2026, 9, 11, 8, tzinfo=UTC), None, timedelta(minutes=10), False),
        (datetime(2026, 9, 11, 8, tzinfo=UTC), None, timedelta(minutes=11), True),
        (
            datetime(2026, 9, 11, 1, tzinfo=UTC),
            PollWindow(start=time(9), end=time(11)),
            timedelta(minutes=2),
            False,
        ),
        (
            datetime(2026, 9, 11, 1, tzinfo=UTC),
            PollWindow(start=time(9), end=time(11)),
            timedelta(minutes=3),
            True,
        ),
        (
            datetime(2026, 9, 11, 18, tzinfo=UTC),
            PollWindow(start=time(9), end=time(11)),
            timedelta(minutes=30),
            False,
        ),
        (
            datetime(2026, 9, 11, 18, tzinfo=UTC),
            PollWindow(start=time(9), end=time(11)),
            timedelta(minutes=31),
            True,
        ),
    ],
)
async def test_merchant_snapshot_uses_current_poll_interval_for_staleness(
    stored_service: tuple[MatocaService, list[str]],
    monkeypatch: pytest.MonkeyPatch,
    now: datetime,
    window: PollWindow | None,
    age: timedelta,
    expected_stale: bool,
) -> None:
    service, _ = stored_service
    service._shops.save_cycle(
        CollectionWrite(
            merchant_key="sawayaka",
            observed_at=now - age,
            shops=[
                ShopObservation(
                    shop=Shop(id=3272, name="Synthetic Shop", is_open=False),
                    list_fresh=True,
                    detail_fresh=True,
                )
            ],
        )
    )

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            del cls
            return now if tz is None else now.astimezone(tz)

    monkeypatch.setattr(service._shops, "poll_window", lambda merchant_key, at: window)
    monkeypatch.setattr("matoca_service.service.datetime", FixedDatetime)

    snapshot = await service.merchant_snapshot("sawayaka")

    assert snapshot.stale is expected_stale


def test_queue_submission_requires_currently_issuable_shop() -> None:
    shop = Shop(
        id=3272,
        name="synthetic merchant",
        lat=34.0,
        lng=137.0,
        is_open=True,
        is_issuable=False,
    )
    submission = QueueSubmission(shop_id=3272, adult_count=2, child_count=0)

    with pytest.raises(QueueUnavailableError, match="受付状況が変更されました"):
        validate_queue_submission(shop, submission, [])


def test_queue_submission_rejects_second_active_queue() -> None:
    shop = Shop(
        id=3272,
        name="synthetic merchant",
        lat=34.0,
        lng=137.0,
        is_open=True,
        is_issuable=True,
        forms=ShopForms(min_adult=1, max_adult=20, min_child=0, max_child=0),
    )
    submission = QueueSubmission(shop_id=3272, adult_count=2, child_count=0)

    with pytest.raises(QueueUnavailableError, match="すでに受付中"):
        validate_queue_submission(shop, submission, [Waiting(id=125000001)])


class SerializedTestService(MatocaService):
    def __init__(self) -> None:
        self._operation_lock = asyncio.Lock()
        self.active = 0
        self.max_active = 0

    async def _dashboard_unlocked(
        self,
        merchant_key: str,
        keyword: str | None,
        page: int = 1,
    ) -> DashboardData:
        del merchant_key, keyword, page
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return DashboardData(
            merchant="Sawayaka",
            native_access_expires_at=datetime(2026, 9, 17, tzinfo=UTC),
            liff_expires_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
            shops=[],
            waiting=[],
        )


@pytest.mark.asyncio
async def test_dashboard_serializes_token_state_operations() -> None:
    service = SerializedTestService()

    await asyncio.gather(
        service.dashboard("sawayaka", None),
        service.dashboard("sawayaka", None),
    )

    assert service.max_active == 1


class FakeCycleClient:
    def __init__(
        self,
        base_shops: list[Shop],
        details: dict[int, Shop],
        *,
        detail_error: Exception | None = None,
    ) -> None:
        self.base_shops = base_shops
        self.details = details
        self.detail_error = detail_error
        self.events: list[str] = []
        self.detail_locations: dict[int, tuple[str | float | None, str | float | None]] = {}
        self.active_details = 0
        self.max_active_details = 0

    async def list_all_shops(self) -> list[Shop]:
        self.events.append("list")
        return self.base_shops

    async def read_shop_catalog(self) -> ShopCatalog:
        return ShopCatalog(await self.list_all_shops(), complete=True)

    async def get_shop(
        self,
        shop_id: int,
        *,
        lat: str | float | None = None,
        lng: str | float | None = None,
    ) -> Shop:
        assert self.events[0] == "list"
        self.detail_locations[shop_id] = (lat, lng)
        self.active_details += 1
        self.max_active_details = max(self.max_active_details, self.active_details)
        try:
            await asyncio.sleep(0.01)
            if self.detail_error is not None:
                raise self.detail_error
            return self.details[shop_id]
        finally:
            self.active_details -= 1

    async def list_waiting(self) -> list[Waiting]:
        self.events.append("waiting")
        return [Waiting(id=42)]


class CycleReadTestService(MatocaService):
    def __init__(self, client: FakeCycleClient) -> None:
        self._operation_lock = asyncio.Lock()
        self.client = client

    async def _authenticated_read(self, merchant_key: str, operation: Any) -> Any:
        assert merchant_key == "sawayaka"
        return await operation(self.client)


class RetryRaceClient:
    def __init__(self) -> None:
        self.base_shops = [Shop(id=shop_id, name=f"List {shop_id}") for shop_id in range(1, 5)]
        self.attempt = 0
        self.first_attempt_ready = asyncio.Event()
        self.release = asyncio.Event()
        self.first_attempt_details = 0
        self.active_details = 0
        self.max_active_details = 0
        self.retry_started_before_drain = False
        self.waiting_cancelled = False

    async def list_all_shops(self) -> list[Shop]:
        self.attempt += 1
        return self.base_shops

    async def read_shop_catalog(self) -> ShopCatalog:
        return ShopCatalog(await self.list_all_shops(), complete=True)

    async def get_shop(
        self,
        shop_id: int,
        *,
        lat: str | float | None = None,
        lng: str | float | None = None,
    ) -> Shop:
        del lat, lng
        self.active_details += 1
        self.max_active_details = max(self.max_active_details, self.active_details)
        try:
            if self.attempt == 1:
                self.first_attempt_details += 1
                if self.first_attempt_details == 4:
                    self.first_attempt_ready.set()
                try:
                    if shop_id == 1:
                        await self.first_attempt_ready.wait()
                        raise status_error(401)
                    await self.release.wait()
                finally:
                    self.first_attempt_details -= 1
            elif self.first_attempt_details:
                self.retry_started_before_drain = True
            return Shop(id=shop_id, name=f"Detail {shop_id}", is_open=True, is_issuable=True)
        finally:
            self.active_details -= 1

    async def list_waiting(self) -> list[Waiting]:
        if self.attempt == 1:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.waiting_cancelled = True
                raise
        return []


class SimultaneousAuthRetryClient(RetryRaceClient):
    async def get_shop(
        self,
        shop_id: int,
        *,
        lat: str | float | None = None,
        lng: str | float | None = None,
    ) -> Shop:
        del lat, lng
        self.active_details += 1
        self.max_active_details = max(self.max_active_details, self.active_details)
        try:
            if self.attempt == 1:
                self.first_attempt_details += 1
                if self.first_attempt_details == 4:
                    self.first_attempt_ready.set()
                try:
                    await self.first_attempt_ready.wait()
                    raise status_error(401 if shop_id % 2 else 403)
                finally:
                    self.first_attempt_details -= 1
            elif self.first_attempt_details:
                self.retry_started_before_drain = True
            return Shop(id=shop_id, name=f"Detail {shop_id}", is_open=True, is_issuable=True)
        finally:
            self.active_details -= 1


class PrimaryWaitingFailureClient(SimultaneousAuthRetryClient):
    def __init__(self) -> None:
        super().__init__()
        self.waiting_failed = asyncio.Event()

    async def get_shop(
        self,
        shop_id: int,
        *,
        lat: str | float | None = None,
        lng: str | float | None = None,
    ) -> Shop:
        del lat, lng
        self.active_details += 1
        self.max_active_details = max(self.max_active_details, self.active_details)
        try:
            self.first_attempt_details += 1
            if self.first_attempt_details == 4:
                self.first_attempt_ready.set()
            try:
                await self.first_attempt_ready.wait()
                await self.waiting_failed.wait()
                raise status_error(401 if shop_id % 2 else 403)
            finally:
                self.first_attempt_details -= 1
        finally:
            self.active_details -= 1

    async def list_waiting(self) -> list[Waiting]:
        await self.first_attempt_ready.wait()
        self.waiting_failed.set()
        raise status_error(500)


class RetryingCycleReadTestService(CycleReadTestService):
    async def _authenticated_read(self, merchant_key: str, operation: Any) -> Any:
        assert merchant_key == "sawayaka"
        try:
            return await operation(self.client)
        except httpx.HTTPStatusError as error:
            assert error.response.status_code in {401, 403}
            return await operation(self.client)


@pytest.mark.asyncio
async def test_read_collection_cycle_enriches_list_shops_with_at_most_four_details() -> None:
    base_shops = [
        Shop(
            id=shop_id,
            name=f"List {shop_id}",
            address=f"Address {shop_id}",
            current_waiting=shop_id,
            lat=34 + shop_id / 100,
            lng=137 + shop_id / 100,
        )
        for shop_id in range(1, 7)
    ]
    details = {
        shop_id: Shop(
            id=shop_id,
            name=f"Detail {shop_id}",
            current_waiting=999,
            is_open=True,
            is_issuable=True,
        )
        for shop_id in range(1, 7)
    }
    service = CycleReadTestService(FakeCycleClient(base_shops, details))

    cycle = await service.read_collection_cycle("sawayaka")

    assert isinstance(cycle, CollectionCycle)
    assert [item.shop.id for item in cycle.shops] == [1, 2, 3, 4, 5, 6]
    assert [item.shop.name for item in cycle.shops] == [
        f"List {shop_id}" for shop_id in range(1, 7)
    ]
    assert [item.shop.current_waiting for item in cycle.shops] == [1, 2, 3, 4, 5, 6]
    assert all(item.detail_fresh for item in cycle.shops)
    assert cycle.waiting == [Waiting(id=42)]
    assert service.client.max_active_details == 4
    assert service.client.detail_locations == {
        shop_id: (34 + shop_id / 100, 137 + shop_id / 100) for shop_id in range(1, 7)
    }


@pytest.mark.asyncio
async def test_read_collection_cycle_drains_first_attempt_before_unauthorized_retry() -> None:
    client = RetryRaceClient()
    service = RetryingCycleReadTestService(client)

    try:
        cycle = await service.read_collection_cycle("sawayaka")
    finally:
        client.release.set()

    assert len(cycle.shops) == 4
    assert client.first_attempt_details == 0
    assert client.waiting_cancelled is True
    assert client.retry_started_before_drain is False
    assert client.max_active_details <= 4


@pytest.mark.asyncio
async def test_read_collection_cycle_retries_after_simultaneous_detail_auth_failures() -> None:
    client = SimultaneousAuthRetryClient()
    service = RetryingCycleReadTestService(client)

    try:
        cycle = await service.read_collection_cycle("sawayaka")
    finally:
        client.release.set()

    assert len(cycle.shops) == 4
    assert client.attempt == 2
    assert client.first_attempt_details == 0
    assert client.waiting_cancelled is True
    assert client.retry_started_before_drain is False
    assert client.max_active_details <= 4


@pytest.mark.asyncio
async def test_read_collection_cycle_preserves_primary_non_auth_error() -> None:
    client = PrimaryWaitingFailureClient()
    service = CycleReadTestService(client)

    with pytest.raises(httpx.HTTPStatusError) as error:
        await service.read_collection_cycle("sawayaka")

    assert error.value.response.status_code == 500
    assert client.first_attempt_details == 0
    assert client.max_active_details <= 4


@pytest.mark.asyncio
async def test_read_collection_cycle_marks_failed_detail_unobserved_and_keeps_list_waiting() -> (
    None
):
    base_shop = Shop(
        id=1,
        name="List 1",
        current_waiting=10,
        waiting_time={"minutes": 20},
        is_open=True,
        is_issuable=True,
        forms=ShopForms(min_adult=1, max_adult=4),
    )
    service = CycleReadTestService(
        FakeCycleClient([base_shop], {}, detail_error=httpx.ReadTimeout("timeout"))
    )

    cycle = await service.read_collection_cycle("sawayaka")

    item = cycle.shops[0]
    assert item.shop.current_waiting == 10
    assert item.shop.waiting_time is None
    assert item.shop.is_open is False
    assert item.shop.is_issuable is False
    assert item.shop.forms is None
    assert item.detail_fresh is False
    assert item.error_code == "timeout"


def status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/shops/1")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("status", request=request, response=response)


@pytest.mark.asyncio
async def test_detail_429_persists_partial_evidence_and_durable_backoff(tmp_path: Path) -> None:
    class RateLimitedClient(FakeCycleClient):
        def __init__(self) -> None:
            super().__init__(
                [Shop(id=n, name=f"Shop {n}", current_waiting=n) for n in range(1, 10)], {}
            )
            self.launched: list[int] = []

        async def get_shop(
            self,
            shop_id: int,
            *,
            lat: str | float | None = None,
            lng: str | float | None = None,
        ) -> Shop:
            del lat, lng
            self.launched.append(shop_id)
            if shop_id == 2:
                error = status_error(429)
                error.response.headers["Retry-After"] = "180"
                raise error
            return Shop(id=shop_id, name="Detail", is_open=True)

    client = RateLimitedClient()
    reader = CycleReadTestService(client)
    database = Database(tmp_path / "data" / "matoca.db")
    database.initialize()
    repository = ShopRepository(database)
    now = datetime.now(tz=UTC)
    coordinator = CollectionCoordinator(
        MerchantRegistry.model_validate(
            {"merchants": {"sawayaka": MerchantRegistry.load_builtin().merchants["sawayaka"]}}
        ),
        CollectionService(reader, repository),
        PollSchedule(repository),
        repository,
        now=lambda: now,
    )
    await coordinator.run_once()
    state = repository.poll_state("sawayaka")
    assert state.error_code == "rate_limited"
    assert state.retry_at >= now + timedelta(seconds=179)
    assert state.last_success_at is None
    stored = repository.latest("sawayaka")
    assert len(stored) == 9
    assert stored[0].observation.detail_fresh is True
    assert stored[1].observation.error_code == "rate_limited"
    assert stored[-1].observation.current_waiting == 9
    assert stored[-1].observation.detail_fresh is False
    assert len(client.launched) < 9
    await coordinator.run_once()
    assert len(client.launched) < 9


@pytest.mark.asyncio
async def test_detail_429_deadline_survives_failed_observation_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = datetime(2026, 9, 11, 8, tzinfo=UTC)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return current if tz is None else current.astimezone(tz)

    monkeypatch.setattr("matoca_service.service.datetime", FixedDatetime)
    error = status_error(429)
    error.response.headers["Retry-After"] = "180"
    client = FakeCycleClient(
        [Shop(id=1, name="Partial", current_waiting=8)], {}, detail_error=error
    )
    database = Database(tmp_path / "data" / "matoca.db")
    database.initialize()
    repository = ShopRepository(database)
    database.write(
        lambda connection: connection.execute("""
        CREATE TRIGGER fail_observation BEFORE INSERT ON shop_observations
        BEGIN SELECT RAISE(ABORT, 'synthetic observation failure'); END
    """)
    )
    collector = CollectionService(CycleReadTestService(client), repository)
    registry = MerchantRegistry.model_validate(
        {"merchants": {"sawayaka": MerchantRegistry.load_builtin().merchants["sawayaka"]}}
    )
    coordinator = CollectionCoordinator(
        registry, collector, PollSchedule(repository), repository, now=lambda: current
    )
    await coordinator.run_once()
    deadline = datetime(2026, 9, 11, 8, 3, tzinfo=UTC)
    state = repository.poll_state("sawayaka")
    assert state.retry_at == deadline
    assert state.error_code == "rate_limited"
    assert state.last_success_at is None
    assert repository.latest("sawayaka") == []

    # Both forced requests and a restarted coordinator must honor durable Retry-After.
    current += timedelta(minutes=1)
    await coordinator.collect("sawayaka")
    restarted = CollectionCoordinator(
        registry, collector, PollSchedule(repository), repository, now=lambda: current
    )
    await restarted.run_once()
    assert client.events.count("list") == 1

    # Recover the original partial evidence before collecting a newer observation.
    database.write(lambda connection: connection.execute("DROP TRIGGER fail_observation"))
    client.detail_error = None
    client.details[1] = Shop(id=1, name="Current", is_open=True)
    current = deadline
    await restarted.run_once()
    observations = repository.observations("sawayaka", 1, limit=10)
    assert len(observations) == 2
    assert observations[1].observed_at == datetime(2026, 9, 11, 8, tzinfo=UTC)
    assert observations[1].current_waiting == 8
    assert observations[1].error_code == "rate_limited"
    assert repository.poll_state("sawayaka").last_success_at == deadline


@pytest.mark.asyncio
@pytest.mark.parametrize("nonempty_pages", [0, 19, 20])
async def test_paginated_collection_replaces_membership_only_after_authoritative_end(
    tmp_path: Path, nonempty_pages: int
) -> None:
    database = Database(tmp_path / "data" / "matoca.db")
    database.initialize()
    repository = ShopRepository(database)
    repository.save_cycle(
        CollectionWrite(
            "sawayaka",
            datetime(2026, 9, 10, 8, tzinfo=UTC),
            [ShopObservation(Shop(id=21, name="Known omitted member"), True, True)],
        )
    )
    requested_pages: list[int] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path == "/liff/shops":
            page = int(request.url.params["page"])
            requested_pages.append(page)
            content = {
                "shops": [{"id": page, "name": f"Shop {page}"}] if page <= nonempty_pages else []
            }
        elif request.url.path == "/liff/waiting":
            content = []
        else:
            shop_id = int(request.url.path.rsplit("/", 1)[1])
            content = {"shop": {"id": shop_id, "name": f"Shop {shop_id}", "is_open": True}}
        return httpx.Response(200, json={"status": "success", "content": content})

    merchant = (
        MerchantRegistry.load_builtin()
        .merchants["sawayaka"]
        .model_copy(update={"api_base_url": "https://synthetic.example.test"})
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        client = MatocaClient(merchant, http, "synthetic-liff")
        cycle = await CollectionService(CycleReadTestService(client), repository).collect(
            "sawayaka"
        )

    stored_ids = {item.shop.id for item in repository.latest("sawayaka")}
    if nonempty_pages == 20:
        assert 21 in stored_ids, "a capped list must not remove known shops beyond the cap"
        assert len(stored_ids) == 21
        assert cycle.catalog_complete is False
        assert repository.catalog_state("sawayaka").complete is False
    else:
        assert stored_ids == set(range(1, nonempty_pages + 1))
        assert cycle.catalog_complete is True
        assert repository.catalog_state("sawayaka").complete is True
    assert requested_pages == list(range(1, min(nonempty_pages + 1, 20) + 1))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("detail_error", "expected_code"),
    [
        (httpx.ConnectError("offline"), "transport"),
        (MatocaApiError("malformed upstream response"), "malformed_response"),
        (status_error(429), "rate_limited"),
        (status_error(500), "http_500"),
    ],
)
async def test_read_collection_cycle_classifies_non_auth_detail_errors(
    detail_error: Exception,
    expected_code: str,
) -> None:
    service = CycleReadTestService(
        FakeCycleClient(
            [Shop(id=1, name="List 1", current_waiting=10)],
            {},
            detail_error=detail_error,
        )
    )

    cycle = await service.read_collection_cycle("sawayaka")

    assert cycle.shops[0].detail_fresh is False
    assert cycle.shops[0].error_code == expected_code


@pytest.mark.asyncio
@respx.mock
async def test_dashboard_reissues_liff_once_after_matoca_unauthorized(
    tmp_path: Path,
    jwt_factory: JwtFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "line_client.toml"
    config_path.write_text(
        """
host = "legy-jp.line-apps.com"
application = "ANDROIDSECONDARY\\t26.11.0\\tAndroid OS\\t14"
locale = "en_US"
protocol_version = "1"
user_agent = "Line/26.11.0"

""".strip(),
        encoding="utf-8",
    )
    access = jwt_factory(
        {
            "jti": "access",
            "rtid": "family",
            "aud": "LINE",
            "iat": 1_789_000_000,
            "exp": 1_894_000_000,
        }
    )
    refresh = jwt_factory(
        {
            "jti": "family",
            "ati": "access",
            "aud": "LINE",
            "aid": "u-synthetic",
            "iat": 1_789_000_000,
            "exp": 1_925_000_000,
        }
    )
    state_path = tmp_path / "state.json"
    JsonStateStore(state_path).save(
        AppState(
            line=LineState(
                access_token=access,
                refresh_token=refresh,
                adid="device-id",
            ),
            liff_tokens={
                "2006055787-m6P6OJ38": LiffTokenState(
                    access_token="stale-liff",
                    issued_at=datetime(2026, 9, 10, tzinfo=UTC),
                    expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                )
            },
        )
    )

    async def issue_fresh_liff(*args: object, **kwargs: object) -> LiffToken:
        del args, kwargs
        return LiffToken(
            access_token="fresh-liff",
            id_token="id-token",
            context_token="context-token",
            issued_at=datetime(2026, 9, 10, tzinfo=UTC),
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )

    monkeypatch.setattr("matoca_service.line.liff.LiffClient.issue_view", issue_fresh_liff)
    auth_route = respx.post("https://admin.junbanmachi.jp/liff/auth").mock(
        side_effect=[
            httpx.Response(401, json={"status": "error"}),
            httpx.Response(200, json={"status": "success", "code": 200}),
        ]
    )
    respx.get("https://admin.junbanmachi.jp/liff/shops").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "code": 200, "content": {"shops": []}},
        )
    )
    respx.get("https://admin.junbanmachi.jp/liff/waiting").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "code": 200, "content": []},
        )
    )

    result = await MatocaService(
        config_path, state_path, tmp_path / "data" / "matoca.db"
    ).dashboard("sawayaka", None)

    assert result.shops == []
    assert [call.request.headers["authorization"] for call in auth_route.calls] == [
        "Bearer stale-liff",
        "Bearer fresh-liff",
    ]
    assert (
        JsonStateStore(state_path).load().liff_tokens["2006055787-m6P6OJ38"].access_token
        == "fresh-liff"
    )


@pytest.mark.asyncio
@respx.mock
async def test_shop_detail_reissues_liff_once_after_unauthorized(
    tmp_path: Path,
    jwt_factory: JwtFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "line_client.toml"
    config_path.write_text(
        """
host = "legy-jp.line-apps.com"
application = "ANDROIDSECONDARY\\t26.11.0\\tAndroid OS\\t14"
locale = "en_US"
protocol_version = "1"
user_agent = "Line/26.11.0"

""".strip(),
        encoding="utf-8",
    )
    access = jwt_factory(
        {
            "jti": "access",
            "rtid": "family",
            "aud": "LINE",
            "iat": 1_789_000_000,
            "exp": 1_894_000_000,
        }
    )
    refresh = jwt_factory(
        {
            "jti": "family",
            "ati": "access",
            "aud": "LINE",
            "aid": "u-synthetic",
            "iat": 1_789_000_000,
            "exp": 1_925_000_000,
        }
    )
    state_path = tmp_path / "state.json"
    JsonStateStore(state_path).save(
        AppState(
            line=LineState(
                access_token=access,
                refresh_token=refresh,
                adid="device-id",
            ),
            liff_tokens={
                "2006055787-m6P6OJ38": LiffTokenState(
                    access_token="stale-liff",
                    issued_at=datetime(2026, 9, 10, tzinfo=UTC),
                    expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                )
            },
        )
    )

    async def issue_fresh_liff(*args: object, **kwargs: object) -> LiffToken:
        del args, kwargs
        return LiffToken(
            access_token="fresh-liff",
            id_token="synthetic-id-token",
            context_token="synthetic-context-token",
            issued_at=datetime(2026, 9, 10, tzinfo=UTC),
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )

    monkeypatch.setattr("matoca_service.line.liff.LiffClient.issue_view", issue_fresh_liff)
    auth_route = respx.post("https://admin.junbanmachi.jp/liff/auth").mock(
        side_effect=[
            httpx.Response(401, json={"status": "error"}),
            httpx.Response(200, json={"status": "success", "code": 200}),
        ]
    )
    detail_route = respx.get("https://admin.junbanmachi.jp/liff/shops/3272").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "code": 200,
                "content": {"shop": {"id": 3272, "name": "synthetic merchant"}},
            },
        )
    )

    result = await MatocaService(
        config_path, state_path, tmp_path / "data" / "matoca.db"
    ).shop_detail("sawayaka", 3272)

    assert result.id == 3272
    assert len(auth_route.calls) == 2
    assert len(detail_route.calls) == 1
    assert [call.request.headers["authorization"] for call in auth_route.calls] == [
        "Bearer stale-liff",
        "Bearer fresh-liff",
    ]
    assert (
        JsonStateStore(state_path).load().liff_tokens["2006055787-m6P6OJ38"].access_token
        == "fresh-liff"
    )
