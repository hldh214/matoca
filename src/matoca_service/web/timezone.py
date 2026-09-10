from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = ZoneInfo("Asia/Tokyo")


def parse_timezone(value: str | None) -> ZoneInfo:
    if not value:
        return DEFAULT_TIMEZONE
    try:
        return ZoneInfo(value)
    except ValueError, ZoneInfoNotFoundError:
        return DEFAULT_TIMEZONE


def localize_datetime(value: datetime, timezone: ZoneInfo) -> datetime:
    return value.astimezone(timezone)
