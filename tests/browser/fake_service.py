import asyncio
from datetime import UTC, date, datetime

from matoca_service.analytics.models import (
    FavoriteState,
    HistoryObservation,
    ShopHistory,
    ShopIdentity,
)
from matoca_service.automation.models import AutomationRequest, AutomationTask
from matoca_service.console import MerchantConsoleData, MerchantSummary, ShopConsoleItem
from matoca_service.matoca.models import Shop, ShopForms, Waiting
from matoca_service.prediction.models import Prediction
from matoca_service.service import (
    DashboardData,
    MerchantSnapshot,
    PartyPreferences,
    QueueSubmission,
    UnknownMerchantError,
)
from matoca_service.tracking.models import QueueIntentSummary, QueueObservation, QueueSession

FIXED_NOW = datetime(2026, 9, 10, 8, tzinfo=UTC)


class BrowserFakeService:
    def __init__(self) -> None:
        self.preferences = PartyPreferences(default_adult_count=2, default_child_count=0)
        self.submissions: list[QueueSubmission] = []
        self.automation_requests: list[AutomationRequest] = []
        self._automation_tasks: list[AutomationTask] = []
        self.refresh_calls = 0
        self.console_reads = 0
        self.waiting_reads = 0
        self.queue_reads = 0
        self.favorite_ids: set[int] = set()
        self.favorite_delay_seconds = 0.0
        self.favorite_writes: list[tuple[int, bool]] = []
        self.queue_intents: list[QueueIntentSummary] = []
        self.create_count = 8
        self._waiting: dict[str, list[Waiting]] = {
            "sawayaka": [],
            "la_ohana_yokohamahonmoku": [],
        }
        self._merchants = [
            MerchantSummary(
                key="sawayaka",
                name="炭焼きレストラン さわやか",
                cover_image_url=None,
            ),
            MerchantSummary(
                key="la_ohana_yokohamahonmoku",
                name="ラ・オハナ 横浜本牧",
                cover_image_url=None,
            ),
        ]
        self._available_shop = Shop.model_validate(
            {
                "id": 3272,
                "name": "炭焼きレストラン さわやか",
                "sub_name": "浜松テスト店",
                "lat": 34.7,
                "lng": 137.7,
                "current_waiting": 8,
                "is_open": True,
                "is_issuable": True,
                "forms": {
                    "min_adult": 2,
                    "max_adult": 5,
                    "min_child": 1,
                    "max_child": 3,
                    "confirm_items": [
                        {
                            "enable": True,
                            "title": "注意事項を確認しましたか",
                            "sub_items": [
                                {
                                    "enable": True,
                                    "disabled": False,
                                    "sub_item_index": 1,
                                    "text": "確認しました",
                                }
                            ],
                        }
                    ],
                },
                "waiting_time": {"minutes": 25, "is_more": False},
            }
        )
        self._console_shops = [
            self._console_item(
                self._available_shop,
                status="available",
                status_label="受付可能",
                can_join=True,
            ),
            self._console_item(
                Shop(id=3273, name="炭焼きレストラン さわやか", sub_name="休業テスト店"),
                status="closed",
                status_label="営業時間外",
            ),
            self._console_item(
                Shop(id=3274, name="炭焼きレストラン さわやか", sub_name="受付停止テスト店"),
                status="suspended",
                status_label="受付停止",
            ),
            self._console_item(
                Shop(id=3275, name="炭焼きレストラン さわやか", sub_name="更新待ちテスト店"),
                status="stale",
                status_label="更新待ち",
                stale=True,
            ),
        ]
        self._console_shops[1].official_waiting_minutes = 40
        self._console_shops[0].prediction = Prediction(
            fast_minutes=20,
            typical_minutes=30,
            confidence="medium",
            effective_samples=7.5,
            level="shop_daypart",
        )
        # Live detail is deliberately stricter than cached list limits and the
        # global settings range (0..20), so the workflow must use the detail API.
        self._console_shops[0].forms = ShopForms(min_adult=1, max_adult=6, min_child=0, max_child=4)

    async def automation_tasks(self) -> list[AutomationTask]:
        return self._automation_tasks

    async def create_automation_task(self, request: AutomationRequest) -> AutomationTask:
        self.automation_requests.append(request)
        task = AutomationTask(
            **request.model_dump(),
            id=str(len(self.automation_requests)),
            shop_name="浜松テスト店",
            created_at=FIXED_NOW,
            form_signature="synthetic",
            next_evaluation_at=FIXED_NOW,
        )
        self._automation_tasks.append(task)
        return task

    async def cancel_automation_task(self, task_id: str) -> AutomationTask:
        task = next(item for item in self._automation_tasks if item.id == task_id)
        task.state = "cancelled"
        task.last_decision = "監視を取り消しました"
        task.next_evaluation_at = None
        return task

    async def resolve_automation_task(self, task_id: str) -> AutomationTask:
        return await self.cancel_automation_task(task_id)

    async def resolve_manual_intent(self, intent_id: str) -> None:
        self.queue_intents = [item for item in self.queue_intents if item.intent_id != intent_id]

    @staticmethod
    def _console_item(
        shop: Shop,
        *,
        status: str,
        status_label: str,
        can_join: bool = False,
        stale: bool = False,
    ) -> ShopConsoleItem:
        return ShopConsoleItem.model_validate(
            {
                "id": shop.id,
                "name": shop.name,
                "sub_name": shop.sub_name,
                "address": "静岡県浜松市テスト町1-1",
                "image_url": shop.image_url,
                "current_waiting": shop.current_waiting,
                "official_waiting_minutes": (
                    shop.waiting_time.minutes if shop.waiting_time is not None else None
                ),
                "official_waiting_is_more": (
                    shop.waiting_time.is_more if shop.waiting_time is not None else False
                ),
                "status": status,
                "status_label": status_label,
                "can_join": can_join,
                "stale": stale,
                "updated_at": FIXED_NOW,
                "forms": shop.forms,
            }
        )

    def _merchant(self, merchant_key: str) -> MerchantSummary:
        for merchant in self._merchants:
            if merchant.key == merchant_key:
                return merchant
        raise UnknownMerchantError(merchant_key)

    def list_merchants(self) -> list[MerchantSummary]:
        return list(self._merchants)

    async def party_preferences(self) -> PartyPreferences:
        return self.preferences

    async def update_party_preferences(
        self,
        party_preferences: PartyPreferences,
    ) -> PartyPreferences:
        self.preferences = party_preferences
        return party_preferences

    async def merchant_snapshot(
        self,
        merchant_key: str,
        *,
        force_catalog: bool = False,
    ) -> MerchantSnapshot:
        del force_catalog
        merchant = self._merchant(merchant_key)
        self.refresh_calls += 1
        return MerchantSnapshot(
            merchant=merchant,
            refreshed_at=FIXED_NOW,
            shops=[self._available_shop] if merchant_key == "sawayaka" else [],
            waiting=list(self._waiting[merchant_key]),
        )

    async def merchant_console(self, merchant_key: str) -> MerchantConsoleData:
        merchant = self._merchant(merchant_key)
        self.console_reads += 1
        shops = list(self._console_shops) if merchant_key == "sawayaka" else []
        return MerchantConsoleData(
            merchant=merchant,
            updated_at=FIXED_NOW,
            stale=any(shop.stale for shop in shops),
            available_count=sum(shop.can_join for shop in shops),
            total_count=len(shops),
            shops=shops,
        )

    async def dashboard(
        self,
        merchant_key: str,
        keyword: str | None,
        page: int = 1,
    ) -> DashboardData:
        merchant = self._merchant(merchant_key)
        shops = [self._available_shop] if merchant_key == "sawayaka" else []
        if keyword:
            shops = [
                shop
                for shop in shops
                if keyword.casefold() in " ".join((shop.name, shop.sub_name or "")).casefold()
            ]
        return DashboardData(
            merchant=merchant.name,
            native_access_expires_at=FIXED_NOW,
            liff_expires_at=FIXED_NOW,
            page=page,
            shops=shops,
            waiting=list(self._waiting[merchant_key]),
        )

    async def shop_detail(self, merchant_key: str, shop_id: int) -> Shop:
        self._merchant(merchant_key)
        if merchant_key == "sawayaka" and shop_id == self._available_shop.id:
            return self._available_shop
        raise LookupError(shop_id)

    async def waiting_detail(self, merchant_key: str, waiting_id: int) -> Waiting:
        self._merchant(merchant_key)
        for waiting in self._waiting[merchant_key]:
            if waiting.id == waiting_id:
                return waiting
        raise LookupError(waiting_id)

    async def current_waiting(self, merchant_key: str) -> list[Waiting]:
        self._merchant(merchant_key)
        self.waiting_reads += 1
        return list(self._waiting[merchant_key])

    async def queues(self) -> list[QueueSession | QueueIntentSummary]:
        self.queue_reads += 1
        merchants = {item.key: item.name for item in self._merchants}
        return [
            QueueSession(
                session_id=waiting.id,
                merchant_key=merchant_key,
                merchant_name=merchants[merchant_key],
                shop_id=int(waiting.shop_id),
                shop_name=self._available_shop.sub_name,
                waiting_id=waiting.id,
                number=waiting.number,
                adult_count=waiting.adult_count,
                child_count=waiting.child_count,
                source="manual",
                submitted_at=FIXED_NOW,
                first_observed_at=FIXED_NOW,
                official_minutes_at_submission=25,
                official_is_more_at_submission=False,
                called_at=None,
                cancelled_at=None,
                status="active",
                prediction=Prediction(
                    fast_minutes=20,
                    typical_minutes=30,
                    confidence="medium",
                    effective_samples=7.5,
                    level="shop_daypart",
                ),
                observations=[QueueObservation(observed_at=FIXED_NOW, count=waiting.count)],
            )
            for merchant_key, items in self._waiting.items()
            for waiting in items
        ] + self.queue_intents

    async def create_waiting(
        self,
        merchant_key: str,
        submission: QueueSubmission,
    ) -> Waiting:
        self._merchant(merchant_key)
        self.submissions.append(submission)
        waiting = Waiting.model_validate(
            {
                "id": 900000000 + len(self.submissions),
                "shop_id": submission.shop_id,
                "adult_count": submission.adult_count,
                "child_count": submission.child_count,
                "number": 101,
                "count": self.create_count,
                "waiting_time": {"minutes": 25, "is_more": False},
            }
        )
        self._waiting[merchant_key].append(waiting)
        return waiting

    async def cancel_waiting(self, merchant_key: str, waiting_id: int) -> None:
        self._merchant(merchant_key)
        self._waiting[merchant_key] = [
            waiting for waiting in self._waiting[merchant_key] if waiting.id != waiting_id
        ]

    async def favorites(self) -> dict[str, list[int]]:
        return {"sawayaka": sorted(self.favorite_ids)} if self.favorite_ids else {}

    async def set_favorite(self, merchant_key: str, shop_id: int, enabled: bool) -> FavoriteState:
        self._merchant(merchant_key)
        self.favorite_writes.append((shop_id, enabled))
        await asyncio.sleep(self.favorite_delay_seconds)
        if enabled:
            self.favorite_ids.add(shop_id)
        else:
            self.favorite_ids.discard(shop_id)
        return FavoriteState(merchant_key=merchant_key, shop_id=shop_id, enabled=enabled)

    async def shop_history(self, merchant_key: str, shop_id: int, day: date) -> ShopHistory:
        self._merchant(merchant_key)
        shop = next(item for item in self._console_shops if item.id == shop_id)
        observations = (
            []
            if day != date(2026, 9, 10)
            else [
                HistoryObservation(
                    observed_at=datetime(2026, 9, 10, 1, minute, tzinfo=UTC),
                    current_waiting=waiting,
                    official_waiting_minutes=minutes,
                    official_waiting_is_more=False,
                    error_code=error,
                    prediction=(
                        Prediction(
                            fast_minutes=max(0, (minutes or 0) - 5),
                            typical_minutes=minutes or 0,
                            confidence="low",
                            effective_samples=2.0,
                            level="shop",
                        )
                        if minutes is not None and error is None
                        else None
                    ),
                )
                for minute, waiting, minutes, error in [
                    (0, 2, 10, None),
                    (5, 4, 20, None),
                    (10, 5, None, "timeout"),
                    (15, 8, 35, None),
                ]
            ]
        )
        if observations:
            observations[-1].official_waiting_is_more = True
        return ShopHistory(
            day=day,
            shop=ShopIdentity(
                id=shop.id, name=shop.name, sub_name=shop.sub_name, address=shop.address
            ),
            observations=observations,
        )
