from dataclasses import dataclass
from datetime import datetime, time

from matoca_service.matoca.models import Shop


@dataclass(frozen=True)
class ShopObservation:
    shop: Shop
    list_fresh: bool
    detail_fresh: bool
    error_code: str | None = None
    observed_at: datetime | None = None

    @property
    def current_waiting(self) -> int:
        return self.shop.current_waiting

    @property
    def waiting_minutes(self) -> int | None:
        if not self.detail_fresh or self.shop.waiting_time is None:
            return None
        return self.shop.waiting_time.minutes

    @property
    def waiting_is_more(self) -> bool:
        return (
            self.detail_fresh
            and self.shop.waiting_time is not None
            and self.shop.waiting_time.is_more
        )

    @property
    def is_open(self) -> bool | None:
        return self.shop.is_open if self.detail_fresh else None

    @property
    def is_issuable(self) -> bool | None:
        return self.shop.is_issuable if self.detail_fresh else None

    @property
    def is_holiday(self) -> bool:
        return self.shop.is_holiday

    @property
    def is_suspended(self) -> bool:
        return self.shop.is_suspended


@dataclass(frozen=True)
class StoredShop:
    merchant_key: str
    shop: Shop
    observation: ShopObservation | None
    last_detail_at: datetime | None


@dataclass(frozen=True)
class CollectionWrite:
    merchant_key: str
    observed_at: datetime
    shops: list[ShopObservation]
    catalog_complete: bool = True


@dataclass(frozen=True)
class CatalogState:
    observed_at: datetime
    complete: bool
    static_refreshed_at: datetime | None = None


@dataclass(frozen=True)
class UserPreferences:
    default_adult_count: int = 2
    default_child_count: int = 0
    early_tolerance_minutes: int = 15
    model_error_minutes: int = 15


@dataclass(frozen=True)
class MerchantPollState:
    merchant_key: str
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    retry_at: datetime | None = None
    error_code: str | None = None
    failure_count: int = 0

    def __post_init__(self) -> None:
        if self.failure_count < 0:
            raise ValueError("failure_count must be nonnegative")


@dataclass(frozen=True)
class PollWindow:
    start: time
    end: time
    crosses_midnight: bool = False


@dataclass(frozen=True)
class RetentionResult:
    raw_deleted: int


@dataclass(frozen=True)
class ObservationRollup:
    merchant_key: str
    shop_id: int
    observed_at: datetime
    sample_count: int
    minimum_waiting: int | None
    maximum_waiting: int | None
    average_waiting: float | None
    waiting_minutes_sample_count: int
    minimum_waiting_minutes: int | None
    maximum_waiting_minutes: int | None
    average_waiting_minutes: float | None
