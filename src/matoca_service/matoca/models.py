from typing import Any

from pydantic import BaseModel, ConfigDict


class MatocaModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Shop(MatocaModel):
    id: int
    name: str
    sub_name: str | None = None
    address: str | None = None
    tel: str | None = None
    lat: str | float | None = None
    lng: str | float | None = None
    image_url: str | None = None
    distance: str | None = None
    current_waiting: int = 0
    is_holiday: bool = False
    is_suspended: bool = False
    options: dict[str, Any] = {}


class Waiting(MatocaModel):
    id: int
