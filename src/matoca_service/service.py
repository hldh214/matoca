import asyncio
import sqlite3
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from functools import cached_property
from pathlib import Path
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from matoca_service.analytics.models import FavoriteState, ShopHistory
from matoca_service.analytics.repository import AnalyticsRepository
from matoca_service.automation.coordinator import AutomationCoordinator
from matoca_service.automation.decisions import TimingDecision
from matoca_service.automation.models import (
    AutomationEditContext,
    AutomationEditRequest,
    AutomationRequest,
    AutomationTask,
)
from matoca_service.automation.replay import ReplayRequest, ReplayResult, replay
from matoca_service.automation.repository import AutomationRepository
from matoca_service.automation.runner import AutomationRunner
from matoca_service.collection.coordinator import CollectionCoordinator
from matoca_service.collection.models import CollectedShop, CollectionCycle, CollectionRateLimited
from matoca_service.collection.schedule import PollSchedule
from matoca_service.collection.service import CollectionService
from matoca_service.config import LineConfig, MerchantConfig, MerchantRegistry
from matoca_service.console import MerchantConsoleData, build_console
from matoca_service.console import MerchantSummary as MerchantSummary
from matoca_service.line.liff import LiffClient
from matoca_service.line.refresh import LineRefreshClient
from matoca_service.line.token_manager import TokenManager
from matoca_service.matoca.client import MatocaApiError, MatocaClient
from matoca_service.matoca.models import CreateWaitingRequest, Shop, ShopOptions, Waiting
from matoca_service.notifications.keys import VapidKeys
from matoca_service.notifications.repository import NotificationRepository
from matoca_service.notifications.service import NotificationService
from matoca_service.prediction.models import Prediction
from matoca_service.prediction.repository import PredictionRepository, PredictionService
from matoca_service.prediction.trends import TrendSummary
from matoca_service.state.store import JsonStateStore
from matoca_service.storage.asyncio import run_storage
from matoca_service.storage.database import Database
from matoca_service.storage.models import UserPreferences
from matoca_service.storage.repositories import PreferenceRepository, ShopRepository
from matoca_service.tracking.coordinator import QueueTrackingCoordinator
from matoca_service.tracking.models import QueueIntent, QueueIntentSummary, QueueSession
from matoca_service.tracking.repository import QueueRepository

T = TypeVar("T")
QUEUE_OBSERVATION_FRESHNESS = timedelta(minutes=3)
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


class ReceptionUnavailableError(QueueUnavailableError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class QueueOutcomeUnknownError(RuntimeError):
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


class PartyPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_adult_count: int
    default_child_count: int

    @field_validator("default_adult_count", "default_child_count")
    @classmethod
    def validate_party_count(cls, value: int) -> int:
        if not 0 <= value <= 20:
            raise PydanticCustomError(
                "party_count_range",
                "人数は0人から20人の範囲で指定してください",
            )
        return value


def validate_queue_submission(
    shop: Shop,
    submission: QueueSubmission,
    current_waiting: list[Waiting],
) -> None:
    if current_waiting:
        raise QueueUnavailableError("すでに受付中の順番待ちがあります")
    if shop.is_holiday:
        raise ReceptionUnavailableError("shop_holiday", "本日は休業です")
    if shop.is_suspended:
        raise ReceptionUnavailableError("shop_suspended", "受付を一時停止しています")
    if not shop.is_open:
        raise ReceptionUnavailableError("shop_closed", "現在は営業時間外です")
    if not shop.is_issuable:
        raise ReceptionUnavailableError("reception_closed", "現在は順番待ちを受け付けていません")
    if shop.lat is None or shop.lng is None:
        raise QueueUnavailableError("店舗の位置情報を取得できません")
    if shop.forms is not None:
        if not shop.forms.min_adult <= submission.adult_count <= shop.forms.max_adult:
            raise QueueUnavailableError("大人の人数が受付範囲外です")
        if not shop.forms.min_child <= submission.child_count <= shop.forms.max_child:
            raise QueueUnavailableError("子どもの人数が受付範囲外です")
    _validate_confirmation_answers(shop, submission)


def _confirmation_choices(item: dict[str, object]) -> set[int]:
    title = item.get("title")
    options = item.get("sub_items")
    if not isinstance(title, str) or not title.strip() or not isinstance(options, list):
        raise QueueUnavailableError("選択内容の確認が必要です")
    choices: set[int] = set()
    for option in options:
        if not isinstance(option, dict) or option.get("enable") is not True:
            continue
        disabled = option.get("disabled")
        if disabled is not None and type(disabled) is not bool:
            raise QueueUnavailableError("選択内容の確認が必要です")
        if disabled:
            continue
        index, text = option.get("sub_item_index"), option.get("text")
        if (
            type(index) is not int
            or index < 0
            or index in choices
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise QueueUnavailableError("選択内容の確認が必要です")
        choices.add(index)
    if not choices:
        raise QueueUnavailableError("選択内容の確認が必要です")
    return choices


def _validate_confirmation_answers(shop: Shop, submission: QueueSubmission) -> None:
    forms = shop.forms
    if forms is not None and forms.is_confirm_tel:
        raise QueueUnavailableError("選択内容の確認が必要です")
    items = (forms.model_extra or {}).get("confirm_items") if forms is not None else None
    if items is None:
        items = []
    if not isinstance(items, list):
        raise QueueUnavailableError("選択内容の確認が必要です")
    # Disabled fields retain their original answer slots, as in the live Web form.
    allowed: list[set[int | None]] = [{0, None}, {None}]
    for index, item in enumerate(items):
        if not isinstance(item, dict) or type(item.get("enable")) is not bool:
            raise QueueUnavailableError("選択内容の確認が必要です")
        if not item["enable"]:
            continue
        if index >= len(allowed):
            raise QueueUnavailableError("選択内容の確認が必要です")
        allowed[index] = set(_confirmation_choices(item))
    if submission.answer1 not in allowed[0] or submission.answer2 not in allowed[1]:
        raise QueueUnavailableError("選択内容の確認が必要です")


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
        self.notifications = NotificationService(
            NotificationRepository(self._database), VapidKeys(self._store)
        )
        self._shops = ShopRepository(self._database, record_history=True)
        self._preferences = PreferenceRepository(self._database)
        self._analytics = AnalyticsRepository(self._database)
        self._queues = QueueRepository(self._database)
        self._automation = AutomationRepository(self._database)
        self._collector = CollectionService(self, self._shops)
        self._poll_schedule = PollSchedule(self._shops, use_history=False)
        self._collection_coordinator = CollectionCoordinator(
            self._registry,
            self._collector,
            self._poll_schedule,
            self._shops,
            has_active_task=lambda _: False,
        )
        self._operation_lock = asyncio.Lock()
        self._tracking_coordinator = QueueTrackingCoordinator(
            list(self._registry.merchants), self, self._queues, reader_persists=True
        )

    # Historical prediction analysis remains offline; live automation uses official estimates.
    @cached_property
    def _predictions(self) -> PredictionService:
        return PredictionService(PredictionRepository(self._database))

    @cached_property
    def _automation_runner(self) -> AutomationRunner:
        return AutomationRunner(self, self._automation)

    @cached_property
    def automation_coordinator(self) -> AutomationCoordinator:
        return AutomationCoordinator(self._automation_runner)

    @property
    def collection_coordinator(self) -> CollectionCoordinator:
        return self._collection_coordinator

    @property
    def tracking_coordinator(self) -> QueueTrackingCoordinator:
        return self._tracking_coordinator

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

    async def party_preferences(self) -> PartyPreferences:
        preferences = await run_storage(self._preferences.get)
        return PartyPreferences(
            default_adult_count=preferences.default_adult_count,
            default_child_count=preferences.default_child_count,
        )

    async def update_party_preferences(
        self, party_preferences: PartyPreferences
    ) -> PartyPreferences:
        current = await run_storage(self._preferences.get)
        updated = await run_storage(
            self._preferences.update,
            UserPreferences(
                default_adult_count=party_preferences.default_adult_count,
                default_child_count=party_preferences.default_child_count,
                early_tolerance_minutes=current.early_tolerance_minutes,
                model_error_minutes=current.model_error_minutes,
            ),
        )
        return PartyPreferences(
            default_adult_count=updated.default_adult_count,
            default_child_count=updated.default_child_count,
        )

    async def merchant_snapshot(
        self,
        merchant_key: str,
        *,
        force_catalog: bool = False,
    ) -> MerchantSnapshot:
        self._merchant(merchant_key)
        requested_at = datetime.now(tz=UTC)
        state = await run_storage(self._shops.catalog_state, merchant_key)
        if force_catalog or state is None:
            await self._collection_coordinator.collect(merchant_key, requested_at=requested_at)
        return await run_storage(self._stored_snapshot, merchant_key)

    async def merchant_console(self, merchant_key: str) -> MerchantConsoleData:
        merchant_config = self._merchant(merchant_key)
        now = datetime.now(tz=UTC)
        merchant = MerchantSummary(
            key=merchant_key,
            name=merchant_config.name,
            cover_image_url=(
                str(merchant_config.cover_image_url)
                if merchant_config.cover_image_url is not None
                else None
            ),
        )
        return await run_storage(self._stored_console, merchant, merchant_key, now)

    async def predict(
        self, merchant_key: str, shop_id: int, official_minutes: int | None, at: datetime
    ) -> Prediction | None:
        self._merchant(merchant_key)
        return await run_storage(
            self._predictions.predict, merchant_key, shop_id, official_minutes, at
        )

    async def predict_pre_call(
        self, merchant_key: str, shop_id: int, official_minutes: int | None, at: datetime
    ) -> Prediction | None:
        self._merchant(merchant_key)
        return await run_storage(
            self._predictions.predict_pre_call, merchant_key, shop_id, official_minutes, at
        )

    async def shop_history(self, merchant_key: str, shop_id: int, day: date) -> ShopHistory:
        self._merchant(merchant_key)
        return await run_storage(self._analytics.shop_history, merchant_key, shop_id, day)

    async def shop_trend(self, merchant_key: str, shop_id: int) -> TrendSummary:
        self._merchant(merchant_key)
        return await run_storage(
            self._analytics.trend_summary, merchant_key, shop_id, datetime.now(UTC)
        )

    async def favorites(self) -> dict[str, list[int]]:
        return await run_storage(self._analytics.favorites)

    async def set_favorite(self, merchant_key: str, shop_id: int, enabled: bool) -> FavoriteState:
        self._merchant(merchant_key)
        await run_storage(self._analytics.set_favorite, merchant_key, shop_id, enabled)
        return FavoriteState(merchant_key=merchant_key, shop_id=shop_id, enabled=enabled)

    def _stored_console(
        self,
        merchant: MerchantSummary,
        merchant_key: str,
        now: datetime,
    ) -> MerchantConsoleData:
        stored_shops, catalog, poll_state = self._shops.snapshot(merchant_key)
        stale_after = self._poll_schedule.next_interval(merchant_key, now, False) * 2
        console = build_console(
            merchant,
            stored_shops,
            catalog,
            poll_state,
            now,
            stale_after=stale_after,
        )
        return console

    def _stored_snapshot(
        self,
        merchant_key: str,
    ) -> MerchantSnapshot:
        stored_shops, catalog, poll_state = self._shops.snapshot(merchant_key)
        stale = catalog is None or not catalog.complete or poll_state.error_code is not None
        observations = [shop.observation for shop in stored_shops if shop.observation is not None]
        refreshed_at = catalog.observed_at if catalog else datetime.now(tz=UTC)
        detail_timestamps: list[datetime] = []
        for stored_shop in stored_shops:
            detail_at = stored_shop.last_detail_at
            if detail_at is not None:
                detail_timestamps.append(detail_at)
        latest_detail_at = min(detail_timestamps) if detail_timestamps else None
        stale = stale or any(not observation.detail_fresh for observation in observations)
        stale = stale or len(observations) != len(stored_shops)
        now = datetime.now(tz=UTC)
        stale_after = self._poll_schedule.next_interval(merchant_key, now, False) * 2
        if (
            stored_shops and (latest_detail_at is None or now - latest_detail_at > stale_after)
        ) or now - refreshed_at > stale_after:
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
            lat, lng = await run_storage(self._stored_shop_coordinates, merchant_key, shop_id)
            return await self._authenticated_read(
                merchant_key,
                lambda client: client.get_shop(shop_id, lat=lat, lng=lng),
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

    async def tracking_read(self, merchant_key: str) -> list[Waiting]:
        async with self._operation_lock:
            closed_ids = await run_storage(self._queues.closed_waiting_ids, merchant_key)

            async def read(client: MatocaClient) -> list[Waiting]:
                waiting = await client.list_waiting()
                for index, item in enumerate(waiting):
                    if item.id in closed_ids:
                        continue
                    if item.count is None or item.estimate_time is None:
                        detail = await client.get_waiting(item.id)
                        waiting[index] = Waiting.model_validate(
                            {**item.model_dump(), **detail.model_dump(exclude_unset=True)}
                        )
                return waiting

            waiting = await self._authenticated_read(merchant_key, read)
            await run_storage(
                self._queues.record_waiting, merchant_key, datetime.now(tz=UTC), waiting
            )
            return waiting

    async def queues(self) -> list[QueueSession | QueueIntentSummary]:
        now = datetime.now(tz=UTC)
        current_minute = now.astimezone(UTC).replace(second=0, microsecond=0)
        sessions = await run_storage(self._queues.list_sessions)
        intents = await run_storage(self._queues.list_unfinished_intents)
        merchants = {item.key: item.name for item in self.list_merchants()}
        shop_names: dict[tuple[str, int], str] = {}
        queue_items: list[QueueSession | QueueIntentSummary] = [*sessions, *intents]
        for item in queue_items:
            if item.shop_id is None:
                continue
            for stored in await run_storage(self._shops.latest, item.merchant_key):
                if stored.shop.id == item.shop_id:
                    shop_names[(item.merchant_key, item.shop_id)] = (
                        stored.shop.sub_name or stored.shop.name
                    )
                    break
        enriched_sessions = []
        for session in sessions:
            latest_observed_at = (
                session.observations[-1].observed_at if session.observations else None
            )
            stale = session.stale or (
                session.status in {"active", "called"}
                and (
                    latest_observed_at is None
                    or current_minute - latest_observed_at.astimezone(UTC)
                    > QUEUE_OBSERVATION_FRESHNESS
                )
            )
            session = session.model_copy(update={"stale": stale})
            enriched_sessions.append(
                session.model_copy(
                    update={
                        "merchant_name": merchants.get(session.merchant_key),
                        "shop_name": (
                            shop_names.get((session.merchant_key, session.shop_id))
                            if session.shop_id is not None
                            else None
                        ),
                        "prediction": None,
                    }
                )
            )
        enriched_intents = [
            intent.model_copy(
                update={
                    "merchant_name": merchants.get(intent.merchant_key),
                    "shop_name": shop_names.get((intent.merchant_key, intent.shop_id)),
                }
            )
            for intent in intents
        ]
        return [*enriched_intents, *enriched_sessions]

    async def read_collection_cycle(self, merchant_key: str) -> CollectionCycle:
        async with self._operation_lock:
            observed_at = datetime.now(tz=UTC)

            async def fetch(client: MatocaClient) -> CollectionCycle:
                catalog = await client.read_shop_catalog()
                base_shops = catalog.shops
                semaphore = asyncio.Semaphore(4)
                rate_limit: CollectionRateLimited | None = None

                async def detail(base_shop: Shop) -> CollectedShop:
                    nonlocal rate_limit
                    async with semaphore:
                        if rate_limit is not None:
                            return CollectedShop(
                                _without_detail(base_shop), True, False, "rate_limited"
                            )
                        try:
                            detail_shop = await client.get_shop(
                                base_shop.id,
                                lat=base_shop.lat,
                                lng=base_shop.lng,
                            )
                        except httpx.HTTPStatusError as error:
                            if error.response.status_code in {401, 403}:
                                raise
                            if error.response.status_code == 429:
                                limited = CollectionRateLimited.from_response(
                                    error.response, datetime.now(tz=UTC)
                                )
                                if rate_limit is None or (
                                    limited.retry_at is not None
                                    and (
                                        rate_limit.retry_at is None
                                        or limited.retry_at > rate_limit.retry_at
                                    )
                                ):
                                    rate_limit = limited
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
                    rate_limit=rate_limit,
                    catalog_complete=catalog.complete,
                )

            return await self._authenticated_read(merchant_key, fetch)

    async def create_waiting(self, merchant_key: str, submission: QueueSubmission) -> Waiting:
        async with self._operation_lock:
            return await self._create_waiting_unlocked(merchant_key, submission)

    async def automation_tasks(self) -> list[AutomationTask]:
        return [
            task
            for task in await run_storage(self._automation.list_tasks)
            if task.timing_policy == "official" and task.mode == "live"
        ]

    async def automation_history(self, task_id: str) -> list[TimingDecision]:
        return await run_storage(self._automation.list_decisions, task_id)

    async def automation_replay(self, request: ReplayRequest) -> ReplayResult:
        self._merchant(request.merchant_key)
        return await run_storage(replay, self._database, request, as_of=datetime.now(UTC))

    async def create_automation_task(self, request: AutomationRequest) -> AutomationTask:
        task = await self._automation_runner.create(request)
        self.automation_coordinator.wake()
        return task

    async def cancel_automation_task(self, task_id: str) -> AutomationTask:
        return await self._automation_runner.cancel(task_id)

    async def automation_edit_context(self, task_id: str) -> AutomationEditContext:
        return await self._automation_runner.edit_context(task_id)

    async def edit_automation_task(
        self, task_id: str, request: AutomationEditRequest
    ) -> AutomationTask:
        task = await self._automation_runner.edit(task_id, request)
        self.automation_coordinator.wake()
        return task

    async def resolve_automation_task(self, task_id: str) -> AutomationTask:
        return await self._automation_runner.resolve(task_id)

    async def resolve_manual_intent(self, intent_id: str) -> None:
        async with self._operation_lock:
            intents = await run_storage(self._queues.list_unfinished_intents)
            intent = next((item for item in intents if item.intent_id == intent_id), None)
            if intent is None:
                raise QueueUnavailableError("この受付の確認はすでに終了しています")
            await self._check_account_waiting_unlocked()
            await run_storage(self._queues.confirm_absent_intent, intent_id, datetime.now(UTC))

    async def _check_account_waiting_unlocked(self, *, exclude: str | None = None) -> None:
        for key in self._registry.merchants:
            if key == exclude:
                continue
            waiting = await self._authenticated_read(key, lambda client: client.list_waiting())
            await run_storage(self._queues.record_waiting, key, datetime.now(UTC), waiting)
            if waiting:
                raise QueueUnavailableError("すでに受付中の順番待ちがあります")

    async def _create_waiting_unlocked(
        self,
        merchant_key: str,
        submission: QueueSubmission,
        *,
        before_send: Callable[[Shop], Awaitable[None]] | None = None,
        authorize_send: Callable[[], None] | None = None,
        persist_intent: Callable[[QueueIntent], None] | None = None,
        source: Literal["manual", "automation"] = "manual",
    ) -> Waiting:
        merchant = self._merchant(merchant_key)
        await self._check_account_waiting_unlocked(exclude=merchant_key)
        lat, lng = await run_storage(
            self._stored_shop_coordinates,
            merchant_key,
            submission.shop_id,
        )
        if lat is None or lng is None:
            raise QueueUnavailableError("店舗の位置情報を取得できません")
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
                    client.get_shop(submission.shop_id, lat=lat, lng=lng),
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
            if before_send is not None:
                await before_send(shop)
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
            intent = QueueIntent(
                intent_id=str(uuid.uuid4()),
                merchant_key=merchant_key,
                shop_id=submission.shop_id,
                submitted_at=datetime.now(tz=UTC),
                official_minutes_at_submission=(
                    shop.waiting_time.minutes if shop.waiting_time is not None else None
                ),
                official_is_more_at_submission=(
                    shop.waiting_time.is_more if shop.waiting_time is not None else None
                ),
                adult_count=submission.adult_count,
                child_count=submission.child_count,
                source=source,
            )
            try:
                await run_storage(persist_intent or self._queues.begin_intent, intent)
            except sqlite3.IntegrityError as error:
                raise QueueUnavailableError(
                    "受付結果を確認中です。現在の受付を確認してから操作してください"
                ) from error
            if authorize_send is not None:
                try:
                    authorize_send()
                except Exception:
                    await run_storage(
                        self._queues.mark_intent_failed, intent.intent_id, "pre_send_check_failed"
                    )
                    raise
            try:
                created = await client.create_waiting(request)
            except MatocaApiError as error:
                await run_storage(
                    self._queues.mark_intent_failed, intent.intent_id, "upstream_rejected"
                )
                raise QueueUnavailableError("受付が受理されませんでした") from error
            except httpx.HTTPStatusError as error:
                if 400 <= error.response.status_code < 500:
                    await run_storage(
                        self._queues.mark_intent_failed, intent.intent_id, "upstream_rejected"
                    )
                    raise QueueUnavailableError("受付が受理されませんでした") from error
                await run_storage(
                    self._queues.mark_intent_unresolved,
                    intent.intent_id,
                    "send_outcome_unknown",
                )
                raise QueueOutcomeUnknownError(
                    "受付結果を確認できません。再申込せず、現在の受付を確認してください"
                ) from error
            except Exception as error:
                await run_storage(
                    self._queues.mark_intent_unresolved, intent.intent_id, "send_outcome_unknown"
                )
                raise QueueOutcomeUnknownError(
                    "受付結果を確認できません。再申込せず、現在の受付を確認してください"
                ) from error
            try:
                await run_storage(
                    self._queues.resolve_intent,
                    intent.intent_id,
                    waiting_id=created.id,
                    number=created.number,
                    count=created.count,
                    status=created.status,
                    observed_at=datetime.now(tz=UTC),
                )
            except (sqlite3.Error, OSError) as error:
                with suppress(sqlite3.Error, OSError):
                    await run_storage(
                        self._queues.mark_intent_unresolved,
                        intent.intent_id,
                        "post_send_persistence_failed",
                    )
                raise QueueOutcomeUnknownError(
                    "受付結果を確認できません。再申込せず、現在の受付を確認してください"
                ) from error
            return created

    def _stored_shop_coordinates(
        self,
        merchant_key: str,
        shop_id: int,
    ) -> tuple[str | float | None, str | float | None]:
        for stored_shop in self._shops.latest(merchant_key):
            if stored_shop.shop.id == shop_id:
                return stored_shop.shop.lat, stored_shop.shop.lng
        return None, None

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
                requested_at = datetime.now(tz=UTC)
                await run_storage(
                    self._queues.mark_cancellation_requested,
                    merchant_key,
                    waiting_id,
                    requested_at,
                )
                try:
                    await client.cancel_waiting(waiting_id)
                except MatocaApiError:
                    await run_storage(
                        self._queues.clear_cancellation_requested, merchant_key, waiting_id
                    )
                    raise
                except httpx.HTTPStatusError as error:
                    if 400 <= error.response.status_code < 500:
                        await run_storage(
                            self._queues.clear_cancellation_requested,
                            merchant_key,
                            waiting_id,
                        )
                        raise
                    raise QueueOutcomeUnknownError(
                        "取消結果を確認できません。現在の受付を確認してください"
                    ) from error
                except Exception as error:
                    raise QueueOutcomeUnknownError(
                        "取消結果を確認できません。現在の受付を確認してください"
                    ) from error
                await run_storage(
                    self._queues.mark_cancelled,
                    merchant_key,
                    waiting_id,
                    requested_at,
                )

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
