from dataclasses import dataclass
from datetime import datetime

from matoca_service.matoca.models import Shop, Waiting
from matoca_service.storage.models import CollectionWrite, ShopObservation


@dataclass(frozen=True)
class CollectedShop:
    shop: Shop
    list_fresh: bool
    detail_fresh: bool
    error_code: str | None = None

    def to_storage(self) -> ShopObservation:
        return ShopObservation(
            shop=self.shop,
            list_fresh=self.list_fresh,
            detail_fresh=self.detail_fresh,
            error_code=self.error_code,
        )


@dataclass(frozen=True)
class CollectionCycle:
    merchant_key: str
    observed_at: datetime
    shops: list[CollectedShop]
    waiting: list[Waiting]

    def to_storage(self) -> CollectionWrite:
        return CollectionWrite(
            merchant_key=self.merchant_key,
            observed_at=self.observed_at,
            shops=[shop.to_storage() for shop in self.shops],
        )
