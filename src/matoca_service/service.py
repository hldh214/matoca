import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field

from matoca_service.catalog import CatalogState, MerchantCatalog, ShopCatalogStore
from matoca_service.config import LineConfig, MerchantConfig, MerchantRegistry
from matoca_service.line.liff import LiffClient
from matoca_service.line.refresh import LineRefreshClient
from matoca_service.line.token_manager import TokenManager
from matoca_service.matoca.client import MatocaApiError, MatocaClient
from matoca_service.matoca.models import CreateWaitingRequest, Shop, Waiting
from matoca_service.state.store import JsonStateStore

T = TypeVar("T")
SNAPSHOT_TTL = timedelta(seconds=60)


class UnknownMerchantError(LookupError):
    pass


class QueueUnavailableError(RuntimeError):
    pass


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
        shop_cache_path: Path = Path("./shop_catalog.json"),
    ) -> None:
        self._line_config = LineConfig.from_toml(line_client_path)
        self._registry = MerchantRegistry.load_builtin()
        self._store = JsonStateStore(state_path)
        self._catalog_store = ShopCatalogStore(shop_cache_path)
        self._operation_lock = asyncio.Lock()
        self._snapshots: dict[str, MerchantSnapshot] = {}

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
        async with self._operation_lock:
            self._merchant(merchant_key)
            existing = self._snapshots.get(merchant_key)
            now = datetime.now(tz=UTC)
            if (
                not force_catalog
                and existing is not None
                and now - existing.refreshed_at < SNAPSHOT_TTL
            ):
                return existing
            try:
                snapshot = await self._merchant_snapshot_unlocked(
                    merchant_key,
                    force_catalog=force_catalog,
                )
            except httpx.HTTPError, MatocaApiError, OSError, ValueError:
                if existing is None:
                    raise
                return existing.model_copy(update={"stale": True})
            self._snapshots[merchant_key] = snapshot
            return snapshot

    async def _merchant_snapshot_unlocked(
        self,
        merchant_key: str,
        *,
        force_catalog: bool,
    ) -> MerchantSnapshot:
        now = datetime.now(tz=UTC)
        try:
            catalog_state = self._catalog_store.load()
        except OSError, ValueError:
            catalog_state = CatalogState()
        cached = catalog_state.merchants.get(merchant_key)
        refresh_catalog = force_catalog or cached is None or not cached.fresh_for(now)

        async def fetch(client: MatocaClient) -> tuple[list[Shop], list[Waiting], bool]:
            partial_stale = False
            if refresh_catalog:
                base_shops = await client.list_all_shops()
            else:
                assert cached is not None
                base_shops = cached.shops
            semaphore = asyncio.Semaphore(4)

            previous_snapshot = self._snapshots.get(merchant_key)
            fallback_candidates = (
                previous_snapshot.shops
                if previous_snapshot is not None
                else cached.shops
                if cached is not None
                else []
            )
            fallback_shops = {shop.id: shop for shop in fallback_candidates}

            async def detail(shop: Shop) -> Shop:
                nonlocal partial_stale
                async with semaphore:
                    try:
                        return await client.get_shop(shop.id)
                    except httpx.HTTPStatusError as error:
                        if error.response.status_code in {401, 403}:
                            raise
                        partial_stale = True
                        return fallback_shops.get(shop.id, shop)
                    except httpx.RequestError, MatocaApiError, ValueError:
                        partial_stale = True
                        return fallback_shops.get(shop.id, shop)

            shops, waiting = await asyncio.gather(
                asyncio.gather(*(detail(shop) for shop in base_shops)),
                client.list_waiting(),
            )
            return list(shops), waiting, partial_stale

        shops, waiting, partial_stale = await self._authenticated_read(merchant_key, fetch)
        if refresh_catalog:
            merchants = dict(catalog_state.merchants)
            merchants[merchant_key] = MerchantCatalog(refreshed_at=now, shops=shops)
            try:
                self._catalog_store.save(catalog_state.model_copy(update={"merchants": merchants}))
            except OSError:
                partial_stale = True
        merchant = self._merchant(merchant_key)
        return MerchantSnapshot(
            merchant=MerchantSummary(
                key=merchant_key,
                name=merchant.name,
                cover_image_url=(
                    str(merchant.cover_image_url) if merchant.cover_image_url else None
                ),
            ),
            refreshed_at=now,
            shops=shops,
            waiting=waiting,
            stale=partial_stale,
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
