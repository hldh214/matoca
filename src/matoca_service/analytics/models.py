from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, StrictBool

from matoca_service.prediction.models import Prediction
from matoca_service.prediction.trends import TrendSummary


class ShopIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    name: str
    sub_name: str | None = None
    address: str | None = None
    tel: str | None = None
    lat: str | None = None
    lng: str | None = None


class HistoryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observed_at: datetime
    current_waiting: int | None
    official_waiting_minutes: int | None
    official_waiting_is_more: bool
    error_code: str | None
    prediction: Prediction | None = None


class ShopHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: date
    shop: ShopIdentity
    observations: list[HistoryObservation]
    trend: TrendSummary | None = None


class FavoriteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class FavoriteState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    merchant_key: str
    shop_id: int
    enabled: bool
