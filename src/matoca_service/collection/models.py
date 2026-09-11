from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

from matoca_service.matoca.models import Shop, Waiting
from matoca_service.storage.models import CollectionWrite, ShopObservation


class CollectionRateLimited(RuntimeError):
    def __init__(
        self, retry_after: timedelta | None = None, *, retry_at: datetime | None = None
    ) -> None:
        super().__init__("collection rate limited")
        self.retry_after = retry_after
        self.retry_at = retry_at

    @classmethod
    def from_response(cls, response: httpx.Response, now: datetime) -> CollectionRateLimited:
        value = response.headers.get("Retry-After")
        if value is not None:
            try:
                deadline = now + timedelta(seconds=max(0, int(value)))
            except ValueError, OverflowError:
                try:
                    deadline = parsedate_to_datetime(value).astimezone(UTC)
                except TypeError, ValueError, OverflowError:
                    return cls()
            return cls(retry_at=max(now, deadline))
        return cls()


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
    rate_limit: CollectionRateLimited | None = None
    catalog_complete: bool = True

    def to_storage(self) -> CollectionWrite:
        return CollectionWrite(
            merchant_key=self.merchant_key,
            observed_at=self.observed_at,
            shops=[shop.to_storage() for shop in self.shops],
            catalog_complete=self.catalog_complete,
        )
