from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from matoca_service.collection.schedule import PollSchedule
from matoca_service.storage.models import PollWindow

TOKYO = ZoneInfo("Asia/Tokyo")


class WindowRepository:
    def __init__(self, window: PollWindow | None) -> None:
        self.window = window

    def poll_window(self, merchant_key: str, now: datetime) -> PollWindow | None:
        return self.window


def at_local_time(hour: int, minute: int) -> datetime:
    return datetime(2026, 9, 10, hour, minute, tzinfo=TOKYO)


def test_new_merchant_polls_every_five_minutes() -> None:
    interval = PollSchedule(WindowRepository(None)).next_interval(
        "new", at_local_time(12, 0), False
    )

    assert interval == timedelta(minutes=5)


def test_known_business_window_polls_every_minute() -> None:
    repository = WindowRepository(PollWindow(start=time(10, 30), end=time(23, 0)))

    assert PollSchedule(repository).next_interval(
        "sawayaka", at_local_time(12, 0), False
    ) == timedelta(minutes=1)


def test_outside_window_polls_every_fifteen_minutes() -> None:
    repository = WindowRepository(PollWindow(start=time(10, 30), end=time(23, 0)))

    assert PollSchedule(repository).next_interval(
        "sawayaka", at_local_time(3, 0), False
    ) == timedelta(minutes=15)


def test_midnight_window_uses_explicit_crossing_semantics() -> None:
    schedule = PollSchedule(
        WindowRepository(PollWindow(start=time(23, 30), end=time(0, 30), crosses_midnight=True))
    )

    assert schedule.next_interval("sawayaka", at_local_time(23, 45), False) == timedelta(minutes=1)
    assert schedule.next_interval("sawayaka", at_local_time(0, 15), False) == timedelta(minutes=1)
    assert schedule.next_interval("sawayaka", at_local_time(12, 0), False) == timedelta(minutes=15)


def test_active_task_polls_every_minute_without_history() -> None:
    assert PollSchedule(WindowRepository(None)).next_interval(
        "new", at_local_time(3, 0), True
    ) == timedelta(minutes=1)
