from pydantic import BaseModel, ConfigDict, Field


class MatocaModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class WaitingOptions(MatocaModel):
    enabled: bool = False
    show_waiting: bool = False
    unit: str | None = None
    is_display_waiting_group: bool = False
    is_display_waiting_time: bool = False


class ShopOptions(MatocaModel):
    waiting: WaitingOptions | None = None


class ShopForms(MatocaModel):
    is_ticketing_only: bool = False
    is_confirm_tel: bool = False
    min_adult: int = 0
    max_adult: int = 0
    default_value_adult: int = 0
    min_child: int = 0
    max_child: int = 0
    default_value_child: int = 0
    is_confirm_child: bool = False


class WaitingEstimate(MatocaModel):
    minutes: int
    is_more: bool = False


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
    forms: ShopForms | None = None
    options: ShopOptions = Field(default_factory=ShopOptions)
    waiting_time: WaitingEstimate | None = None
    is_issuable: bool = False
    is_open: bool = False
    is_issuable_area: bool = False
    next_reception_time: str | None = None
    ticketing_button_text: str | None = None


class Waiting(MatocaModel):
    id: int
    count: int | None = None
    number: int | None = None
    shop_id: int | str | None = None
    adult_count: int | None = None
    child_count: int | None = None
    status: str | None = None


class CreateWaitingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_id: str
    adult_count: int
    child_count: int
    answer1: int | None = 0
    answer2: int | None = None
    lat: float
    lng: float
    in_advance_information: str = ""
    ref: str = "web"
