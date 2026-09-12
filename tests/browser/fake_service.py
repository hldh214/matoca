from datetime import UTC, datetime

from matoca_service.console import MerchantConsoleData, MerchantSummary, ShopConsoleItem
from matoca_service.matoca.models import Shop, ShopForms, Waiting
from matoca_service.service import (
    DashboardData,
    MerchantSnapshot,
    PartyPreferences,
    QueueSubmission,
    UnknownMerchantError,
)

FIXED_NOW = datetime(2026, 9, 10, 8, tzinfo=UTC)


class BrowserFakeService:
    def __init__(self) -> None:
        self.preferences = PartyPreferences(default_adult_count=2, default_child_count=0)
        self.submissions: list[QueueSubmission] = []
        self.refresh_calls = 0
        self.console_reads = 0
        self.waiting_reads = 0
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
        # Live detail is deliberately stricter than cached list limits and the
        # global settings range (0..20), so the workflow must use the detail API.
        self._console_shops[0].forms = ShopForms(min_adult=1, max_adult=6, min_child=0, max_child=4)

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
                "count": 8,
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
