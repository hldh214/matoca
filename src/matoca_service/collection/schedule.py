from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from matoca_service.storage.models import PollWindow

TOKYO = ZoneInfo("Asia/Tokyo")


class PollWindowRepository(Protocol):
    def poll_window(self, merchant_key: str, now: datetime) -> PollWindow | None: ...


class PollSchedule:
    def __init__(self, repository: PollWindowRepository) -> None:
        self._repository = repository

    def next_interval(
        self,
        merchant_key: str,
        now: datetime,
        has_active_task: bool,
    ) -> timedelta:
        if has_active_task:
            return timedelta(minutes=1)

        window = self._repository.poll_window(merchant_key, now)
        if window is None:
            return timedelta(minutes=5)
        if _in_window(window, now):
            return timedelta(minutes=1)
        return timedelta(minutes=15)


def _in_window(window: PollWindow, now: datetime) -> bool:
    local_now = now.astimezone(TOKYO)
    minute = local_now.hour * 60 + local_now.minute
    start = window.start.hour * 60 + window.start.minute
    end = window.end.hour * 60 + window.end.minute
    if window.crosses_midnight:
        return minute >= start or minute <= end
    return start <= minute <= end
