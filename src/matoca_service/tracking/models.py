from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

QueueStatus = Literal["active", "called", "cancelled", "unknown"]
QueueSource = Literal["manual", "automation", "adopted"]


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
    observations: list[QueueObservation]
