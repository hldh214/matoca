import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field

from matoca_service.collection.coordinator import CollectionCoordinator
from matoca_service.collection.models import CollectedShop, CollectionCycle
from matoca_service.collection.schedule import PollSchedule
from matoca_service.collection.service import CollectionService
from matoca_service.config import LineConfig, MerchantConfig, MerchantRegistry
from matoca_service.line.liff import LiffClient
from matoca_service.line.refresh import LineRefreshClient
from matoca_service.line.token_manager import TokenManager
from matoca_service.matoca.client import MatocaApiError, MatocaClient
from matoca_service.matoca.models import CreateWaitingRequest, Shop, ShopOptions, Waiting
from matoca_service.state.store import JsonStateStore
from matoca_service.storage.database import Database
from matoca_service.storage.models import StoredShop
from matoca_service.storage.repositories import ShopRepository

T = TypeVar("T")
_LIST_IDENTITY_FIELDS = (
    "id",
    "name",
    "sub_name",
    "address",
    "tel",
    "lat",
    "lng",
    "image_url",
    "distance",
    "current_waiting",
)


class UnknownMerchantError(LookupError):
    pass


class QueueUnavailableError(RuntimeError):
    pass


def _merge_list_shop(base_shop: Shop, detail_shop: Shop) -> Shop:
    return detail_shop.model_copy(
        update={field: getattr(base_shop, field) for field in _LIST_IDENTITY_FIELDS}
    )


def _without_detail(base_shop: Shop) -> Shop:
    return base_shop.model_copy(
        update={
            "forms": None,
            "options": ShopOptions(),
            "waiting_time": None,
            "is_issuable": False,
            "is_open": False,
            "is_issuable_area": False,
            "next_reception_time": None,
            "ticketing_button_text": None,
        }
    )


def _detail_error_code(error: Exception) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.RequestError):
        return "transport"
    if isinstance(error, MatocaApiError | ValueError):
        return "malformed_response"
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        return "rate_limited" if status_code == 429 else f"http_{status_code}"
    raise TypeError(f"unsupported detail error: {type(error).__name__}")


class DashboardData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant: str
    native_access_expires_at: datetime
    liff_expires_at: datetime
    page: int = 1
    shops: list[Shop]
    waiting: list[Waiting]


class MerchantSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    cover_image_url: str | None = None


class MerchantSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant: MerchantSummary
    refreshed_at: datetime
    shops: list[Shop]
    waiting: list[Waiting]
    stale: bool = False


class QueueSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_id: int = Field(ge=1)
    adult_count: int = Field(ge=0)
    child_count: int = Field(ge=0)
    answer1: int | None = 0
    answer2: int | None = None
    in_advance_information: str = ""


def validate_queue_submission(
    shop: Shop,
    submission: QueueSubmission,
    current_waiting: list[Waiting],
) -> None:
    if current_waiting:
        raise QueueUnavailableError("すでに受付中の順番待ちがあります")
    if not shop.is_issuable or not shop.is_open or shop.is_holiday or shop.is_suspended:
        raise QueueUnavailableError("受付状況が変更されました")
    if shop.lat is None or shop.lng is None:
        raise QueueUnavailableError("店舗の位置情報を取得できません")
    if shop.forms is not None:
        if not shop.forms.min_adult <= submission.adult_count <= shop.forms.max_adult:
            raise QueueUnavailableError("大人の人数が受付範囲外です")
        if not shop.forms.min_child <= submission.child_count <= shop.forms.max_child:
            raise QueueUnavailableError("子どもの人数が受付範囲外です")


class MatocaService:
    def __init__(
        self,
        line_client_path: Path,
        state_path: Path,
        database_path: Path,
    ) -> None:
        self._line_config = LineConfig.from_toml(line_client_path)
        self._registry = MerchantRegistry.load_builtin()
        self._store = JsonStateStore(state_path)
        self._database = Database(database_path)
        self._database.initialize()
        self._shops = ShopRepository(self._database)
        self._collector = CollectionService(self, self._shops)
        self._poll_schedule = PollSchedule(self._shops)
        self._collection_coordinator = CollectionCoordinator(
            self._registry,
            self._collector,
            self._poll_schedule,
            self._shops,
        )
        self._operation_lock = asyncio.Lock()

    @property
    def collection_coordinator(self) -> CollectionCoordinator:
        return self._collection_coordinator

    def _merchant(self, merchant_key: str) -> MerchantConfig:
        try:
            return self._registry.merchants[merchant_key]
        except KeyError as error:
            raise UnknownMerchantError(merchant_key) from error

    def list_merchants(self) -> list[MerchantSummary]:
        return [
            MerchantSummary(
                key=key,
                name=merchant.name,
                cover_image_url=(
                    str(merchant.cover_image_url) if merchant.cover_image_url else None
                ),
            )
            for key, merchant in self._registry.merchants.items()
        ]

    async def merchant_snapshot(
        self,
        merchant_key: str,
        *,
        force_catalog: bool = False,
    ) -> MerchantSnapshot:
        self._merchant(merchant_key)
        stored_shops = await asyncio.to_thread(self._shops.latest, merchant_key)
        if force_catalog or not stored_shops:
            try:
                await self._collector.collect(merchant_key)
            except httpx.HTTPError, MatocaApiError, OSError, ValueError:
                if not stored_shops:
                    raise
                return self._stored_snapshot(merchant_key, stored_shops, stale=True)
            stored_shops = await asyncio.to_thread(self._shops.latest, merchant_key)
        if not stored_shops:
            raise RuntimeError("collection completed without storing shops")
        return self._stored_snapshot(merchant_key, stored_shops)

    def _stored_snapshot(
        self,
        merchant_key: str,
        stored_shops: list[StoredShop],
        *,
        stale: bool = False,
    ) -> MerchantSnapshot:
        observations = [shop.observation for shop in stored_shops if shop.observation is not None]
        if not observations:
            raise RuntimeError("stored shops have no collection observations")
        observed_timestamps: list[datetime] = []
        for observation in observations:
            observed_at = observation.observed_at
            if observed_at is not None:
                observed_timestamps.append(observed_at)
        if not observed_timestamps:
            raise RuntimeError("stored observations have no timestamps")
        refreshed_at = max(observed_timestamps)
        latest_cycle = [
            observation for observation in observations if observation.observed_at == refreshed_at
        ]
        detail_timestamps: list[datetime] = []
        for stored_shop in stored_shops:
            detail_at = stored_shop.last_detail_at
            if detail_at is not None:
                detail_timestamps.append(detail_at)
        latest_detail_at = max(detail_timestamps) if detail_timestamps else None
        stale = stale or any(not observation.detail_fresh for observation in latest_cycle)
        now = datetime.now(tz=UTC)
        stale_after = self._poll_schedule.next_interval(merchant_key, now, False) * 2
        if latest_detail_at is None or now - latest_detail_at > stale_after:
            stale = True
        merchant = self._merchant(merchant_key)
        return MerchantSnapshot(
            merchant=MerchantSummary(
                key=merchant_key,
                name=merchant.name,
                cover_image_url=(
                    str(merchant.cover_image_url) if merchant.cover_image_url else None
                ),
            ),
            refreshed_at=refreshed_at,
            shops=[stored.shop for stored in stored_shops],
            waiting=[],
            stale=stale,
        )

    async def dashboard(
        self,
        merchant_key: str,
        keyword: str | None,
        page: int = 1,
    ) -> DashboardData:
        async with self._operation_lock:
            return await self._dashboard_unlocked(merchant_key, keyword, page)

    async def shop_detail(self, merchant_key: str, shop_id: int) -> Shop:
        async with self._operation_lock:
            return await self._authenticated_read(
                merchant_key,
                lambda client: client.get_shop(shop_id),
            )

    async def waiting_detail(self, merchant_key: str, waiting_id: int) -> Waiting:
        async with self._operation_lock:
            return await self._authenticated_read(
                merchant_key,
                lambda client: client.get_waiting(waiting_id),
            )

    async def current_waiting(self, merchant_key: str) -> list[Waiting]:
        async with self._operation_lock:
            return await self._authenticated_read(
                merchant_key,
                lambda client: client.list_waiting(),
            )

    async def read_collection_cycle(self, merchant_key: str) -> CollectionCycle:
        async with self._operation_lock:
            observed_at = datetime.now(tz=UTC)

            async def fetch(client: MatocaClient) -> CollectionCycle:
                base_shops = await client.list_all_shops()
                semaphore = asyncio.Semaphore(4)

                async def detail(base_shop: Shop) -> CollectedShop:
                    async with semaphore:
                        try:
                            detail_shop = await client.get_shop(base_shop.id)
                        except httpx.HTTPStatusError as error:
                            if error.response.status_code in {401, 403}:
                                raise
                            return CollectedShop(
                                shop=_without_detail(base_shop),
                                list_fresh=True,
                                detail_fresh=False,
                                error_code=_detail_error_code(error),
                            )
                        except httpx.TimeoutException as error:
                            return CollectedShop(
                                shop=_without_detail(base_shop),
                                list_fresh=True,
                                detail_fresh=False,
                                error_code=_detail_error_code(error),
                            )
                        except (httpx.RequestError, MatocaApiError, ValueError) as error:
                            return CollectedShop(
                                shop=_without_detail(base_shop),
                                list_fresh=True,
                                detail_fresh=False,
                                error_code=_detail_error_code(error),
                            )
                        return CollectedShop(
                            shop=_merge_list_shop(base_shop, detail_shop),
                            list_fresh=True,
                            detail_fresh=True,
                        )

                detail_tasks = [asyncio.create_task(detail(shop)) for shop in base_shops]
                waiting_task = asyncio.create_task(client.list_waiting())
                attempt_tasks = [*detail_tasks, waiting_task]
                try:
                    await asyncio.gather(*attempt_tasks)
                except BaseException:
                    for task in attempt_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*attempt_tasks, return_exceptions=True)
                    raise
                return CollectionCycle(
                    merchant_key=merchant_key,
                    observed_at=observed_at,
                    shops=[task.result() for task in detail_tasks],
                    waiting=waiting_task.result(),
                )

            return await self._authenticated_read(merchant_key, fetch)

    async def create_waiting(self, merchant_key: str, submission: QueueSubmission) -> Waiting:
        async with self._operation_lock:
            merchant = self._merchant(merchant_key)
            async with httpx.AsyncClient(http2=True, timeout=30) as http:
                manager = TokenManager(
                    self._store,
                    LineRefreshClient(self._line_config, http),
                    liff_client=LiffClient(self._line_config, http),
                )
                await manager.ensure_native_token()

                async def prepare(*, force_liff: bool) -> tuple[MatocaClient, Shop, list[Waiting]]:
                    liff = await manager.ensure_liff_token(
                        liff_id=merchant.liff_id,
                        merchant=merchant,
                        force=force_liff,
                    )
                    client = MatocaClient(merchant, http, liff.access_token)
                    await client.authenticate()
                    shop, waiting = await asyncio.gather(
                        client.get_shop(submission.shop_id),
                        client.list_waiting(),
                    )
                    return client, shop, waiting

                try:
                    client, shop, waiting = await prepare(force_liff=False)
                except httpx.HTTPStatusError as error:
                    if error.response.status_code not in {401, 403}:
                        raise
                    client, shop, waiting = await prepare(force_liff=True)

                validate_queue_submission(shop, submission, waiting)
                assert shop.lat is not None and shop.lng is not None
                request = CreateWaitingRequest(
                    shop_id=str(shop.id),
                    adult_count=submission.adult_count,
                    child_count=submission.child_count,
                    answer1=submission.answer1,
                    answer2=submission.answer2,
                    lat=float(shop.lat),
                    lng=float(shop.lng),
                    in_advance_information=submission.in_advance_information,
                )
                return await client.create_waiting(request)

    async def cancel_waiting(self, merchant_key: str, waiting_id: int) -> None:
        async with self._operation_lock:
            merchant = self._merchant(merchant_key)
            async with httpx.AsyncClient(http2=True, timeout=30) as http:
                manager = TokenManager(
                    self._store,
                    LineRefreshClient(self._line_config, http),
                    liff_client=LiffClient(self._line_config, http),
                )
                await manager.ensure_native_token()

                async def authenticate(*, force_liff: bool) -> tuple[MatocaClient, list[Waiting]]:
                    liff = await manager.ensure_liff_token(
                        liff_id=merchant.liff_id,
                        merchant=merchant,
                        force=force_liff,
                    )
                    client = MatocaClient(merchant, http, liff.access_token)
                    await client.authenticate()
                    return client, await client.list_waiting()

                try:
                    client, waiting = await authenticate(force_liff=False)
                except httpx.HTTPStatusError as error:
                    if error.response.status_code not in {401, 403}:
                        raise
                    client, waiting = await authenticate(force_liff=True)
                if not any(item.id == waiting_id for item in waiting):
                    raise QueueUnavailableError("取消対象の順番待ちが見つかりません")
                await client.cancel_waiting(waiting_id)

    async def _authenticated_read(
        self,
        merchant_key: str,
        operation: Callable[[MatocaClient], Awaitable[T]],
    ) -> T:
        merchant = self._merchant(merchant_key)
        async with httpx.AsyncClient(http2=True, timeout=30) as http:
            manager = TokenManager(
                self._store,
                LineRefreshClient(self._line_config, http),
                liff_client=LiffClient(self._line_config, http),
            )
            await manager.ensure_native_token()

            async def fetch(*, force_liff: bool) -> T:
                liff = await manager.ensure_liff_token(
                    liff_id=merchant.liff_id,
                    merchant=merchant,
                    force=force_liff,
                )
                matoca = MatocaClient(merchant, http, liff.access_token)
                await matoca.authenticate()
                return await operation(matoca)

            try:
                return await fetch(force_liff=False)
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in {401, 403}:
                    raise
                return await fetch(force_liff=True)

    async def _dashboard_unlocked(
        self,
        merchant_key: str,
        keyword: str | None,
        page: int = 1,
    ) -> DashboardData:
        merchant = self._merchant(merchant_key)
        async with httpx.AsyncClient(http2=True, timeout=30) as http:
            manager = TokenManager(
                self._store,
                LineRefreshClient(self._line_config, http),
                liff_client=LiffClient(self._line_config, http),
            )
            native = await manager.ensure_native_token()

            async def fetch(*, force_liff: bool) -> tuple[datetime, list[Shop], list[Waiting]]:
                liff = await manager.ensure_liff_token(
                    liff_id=merchant.liff_id,
                    merchant=merchant,
                    force=force_liff,
                )
                matoca = MatocaClient(merchant, http, liff.access_token)
                await matoca.authenticate()
                shops, waiting = await asyncio.gather(
                    matoca.list_shops(page=page, keyword=keyword),
                    matoca.list_waiting(),
                )
                return liff.expires_at, shops, waiting

            try:
                liff_expires_at, shops, waiting = await fetch(force_liff=False)
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in {401, 403}:
                    raise
                liff_expires_at, shops, waiting = await fetch(force_liff=True)
        return DashboardData(
            merchant=merchant.name,
            native_access_expires_at=native.access_expires_at,
            liff_expires_at=liff_expires_at,
            page=page,
            shops=shops,
            waiting=waiting,
        )
