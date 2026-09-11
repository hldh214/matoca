from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from matoca_service.matoca.models import Shop, ShopForms
from matoca_service.storage.models import CatalogState, MerchantPollState, StoredShop

ShopStatus = Literal["available", "closed", "holiday", "suspended", "stale"]


class MerchantSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    cover_image_url: str | None = None


class ShopConsoleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    sub_name: str | None
    address: str | None
    image_url: str | None
    current_waiting: int
    official_waiting_minutes: int | None
    official_waiting_is_more: bool
    status: ShopStatus
    status_label: str
    can_join: bool
    stale: bool
    updated_at: datetime | None
    forms: ShopForms | None


class MerchantConsoleData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant: MerchantSummary
    updated_at: datetime
    stale: bool
    available_count: int
    total_count: int
    shops: list[ShopConsoleItem]


def console_item(
    shop: Shop,
    *,
    detail_fresh: bool,
    observed_at: datetime | None = None,
) -> ShopConsoleItem:
    if not detail_fresh:
        status: ShopStatus = "stale"
        status_label = "更新待ち"
    elif shop.is_holiday:
        status = "holiday"
        status_label = "休業"
    elif not shop.is_open:
        status = "closed"
        status_label = "営業時間外"
    elif shop.is_suspended or not shop.is_issuable:
        status = "suspended"
        status_label = "受付停止"
    else:
        status = "available"
        status_label = "受付可能"

    return ShopConsoleItem(
        id=shop.id,
        name=shop.name,
        sub_name=shop.sub_name,
        address=shop.address,
        image_url=shop.image_url,
        current_waiting=shop.current_waiting,
        official_waiting_minutes=(
            shop.waiting_time.minutes if detail_fresh and shop.waiting_time else None
        ),
        official_waiting_is_more=(
            detail_fresh and shop.waiting_time is not None and shop.waiting_time.is_more
        ),
        status=status,
        status_label=status_label,
        can_join=status == "available",
        stale=status == "stale",
        updated_at=observed_at,
        forms=shop.forms,
    )


def build_console(
    merchant: MerchantSummary,
    stored_shops: list[StoredShop],
    catalog: CatalogState | None,
    poll_state: MerchantPollState,
    now: datetime,
    *,
    stale_after: timedelta,
) -> MerchantConsoleData:
    stale = catalog is None or not catalog.complete or poll_state.error_code is not None
    stale = stale or (catalog is not None and now - catalog.observed_at > stale_after)
    shops = [
        console_item(
            stored_shop.shop,
            detail_fresh=(
                stored_shop.observation is not None
                and stored_shop.observation.detail_fresh
                and stored_shop.last_detail_at is not None
                and now - stored_shop.last_detail_at <= stale_after
            ),
            observed_at=(
                stored_shop.observation.observed_at if stored_shop.observation is not None else None
            ),
        )
        for stored_shop in stored_shops
    ]
    return MerchantConsoleData(
        merchant=merchant,
        updated_at=catalog.observed_at if catalog is not None else now,
        stale=stale or any(item.stale for item in shops),
        available_count=sum(item.can_join for item in shops),
        total_count=len(shops),
        shops=shops,
    )
