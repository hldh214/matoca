from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PredictionConfidence = Literal["low", "medium", "high"]
PredictionLevel = Literal["shop_daypart", "shop", "merchant", "cold_start"]


class PredictionSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    merchant_key: str
    shop_id: int
    submitted_at: datetime
    ratio: float = Field(ge=0)


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fast_minutes: int = Field(ge=0)
    typical_minutes: int = Field(ge=0)
    confidence: PredictionConfidence
    effective_samples: float = Field(ge=0)
    level: PredictionLevel
    target: Literal["calling", "pre_call"] = "calling"
