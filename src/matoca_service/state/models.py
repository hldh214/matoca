from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class LineState(StateModel):
    access_token: str = Field(min_length=1, repr=False)
    refresh_token: str = Field(min_length=1, repr=False)
    access_expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    rtid: str | None = None
    aid: str | None = None
    lsid: str | None = None
    adid: str = Field(min_length=1)
    updated_at: datetime | None = None
    pending_access_report: bool = False


class LiffTokenState(StateModel):
    access_token: str = Field(min_length=1, repr=False)
    expires_at: datetime
    issued_at: datetime | None = None


class AppState(StateModel):
    version: Literal[1] = 1
    line: LineState
    liff_tokens: dict[str, LiffTokenState] = Field(default_factory=dict)
