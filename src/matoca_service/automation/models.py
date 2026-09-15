from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskState = Literal[
    "scheduled",
    "monitoring",
    "submitting",
    "reconciling",
    "queued",
    "completed",
    "cancelled",
    "expired",
    "failed",
    "needs_attention",
    "unknown",
]


class AutomationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_key: str
    shop_id: int = Field(ge=1)
    arrival_at: datetime
    timezone: str = "Asia/Tokyo"
    adult_count: int = Field(default=2, ge=0, le=20)
    child_count: int = Field(default=0, ge=0, le=20)
    answer1: int | None = 0
    answer2: int | None = None
    in_advance_information: str = Field(default="", max_length=1000)
    early_tolerance_minutes: int = Field(default=15, ge=0, le=120)
    model_error_minutes: int = Field(default=15, ge=0, le=120)
    consent: Literal[True]

    @field_validator("arrival_at")
    @classmethod
    def aware_arrival(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("到着予定には時差を含めてください")
        return value.astimezone(UTC)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("タイムゾーンが正しくありません") from error
        return value


class AutomationTask(AutomationRequest):
    id: str
    shop_name: str
    created_at: datetime
    state: TaskState = "scheduled"
    version: int = 0
    intent_id: str | None = None
    form_signature: str = Field(exclude=True)
    last_decision: str = "自動受付を有効にしました"
    evaluated_at: datetime | None = None
    next_evaluation_at: datetime | None = None


class AutomationEvent(BaseModel):
    id: int
    task_id: str
    at: datetime
    state: TaskState
    decision: str
