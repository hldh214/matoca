from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field

from matoca_service.prediction.models import Prediction

QueueStatus = Literal["active", "called", "cancelled", "unknown"]
QueueSource = Literal["manual", "automation", "adopted"]
IntentStatus = Literal["pending", "unresolved"]


class QueueIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str
    merchant_key: str
    shop_id: int
    submitted_at: datetime
    official_minutes_at_submission: int | None
    official_is_more_at_submission: bool | None = None
    adult_count: int
    child_count: int
    source: Literal["manual", "automation"] = "manual"


class QueueRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_key: str
    observed_at: datetime
    waiting_id: int
    shop_id: int | None = None
    number: int | None = None
    count: int | None = None
    adult_count: int | None = None
    child_count: int | None = None


class QueueObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    count: int | None


class QueueIntentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str
    merchant_key: str
    merchant_name: str | None = None
    shop_id: int
    shop_name: str | None = None
    submitted_at: datetime
    official_minutes_at_submission: int | None
    official_is_more_at_submission: bool | None
    adult_count: int
    child_count: int
    source: Literal["manual", "automation"]
    status: IntentStatus
    error_code: str | None = None


class QueueSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: int
    intent_id: str | None = None
    merchant_key: str
    merchant_name: str | None = None
    shop_id: int | None
    shop_name: str | None = None
    waiting_id: int
    number: int | None
    adult_count: int | None
    child_count: int | None
    source: QueueSource
    submitted_at: datetime | None
    first_observed_at: datetime
    official_minutes_at_submission: int | None
    official_is_more_at_submission: bool | None
    called_at: datetime | None
    cancelled_at: datetime | None
    status: QueueStatus
    stale: bool = False
    error_code: str | None = None
    prediction: Prediction | None = None
    observations: list[QueueObservation]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def trajectory_minutes(self) -> int | None:
        valid = [(item, item.count) for item in self.observations if item.count is not None]
        if self.stale or self.status != "active" or len(valid) < 2:
            return None
        (first, first_count), (last, last_count) = valid[0], valid[-1]
        decrease = first_count - last_count
        elapsed = (last.observed_at - first.observed_at).total_seconds() / 60
        if decrease <= 0 or elapsed <= 0 or last_count <= 0:
            return None
        return round(last_count * elapsed / decrease)
