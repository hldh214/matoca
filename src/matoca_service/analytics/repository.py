import sqlite3
from datetime import UTC, date, datetime, time
from typing import cast
from zoneinfo import ZoneInfo

from matoca_service.analytics.models import HistoryObservation, ShopHistory, ShopIdentity
from matoca_service.storage.database import Database

TOKYO = ZoneInfo("Asia/Tokyo")


class AnalyticsRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def shop_history(self, merchant_key: str, shop_id: int, day: date) -> ShopHistory:
        return self._database.read(
            lambda connection: self._shop_history(connection, merchant_key, shop_id, day)
        )

    def _shop_history(
        self, connection: sqlite3.Connection, merchant_key: str, shop_id: int, day: date
    ) -> ShopHistory:
        shop = connection.execute(
            "SELECT shop_id, name, sub_name, address, tel, lat, lng FROM shops "
            "WHERE merchant_key = ? AND shop_id = ?",
            (merchant_key, shop_id),
        ).fetchone()
        if shop is None:
            raise LookupError(shop_id)
        start = datetime.combine(day, time.min, TOKYO).astimezone(UTC)
        end = datetime.combine(day, time.max, TOKYO).astimezone(UTC)
        rows = connection.execute(
            """SELECT observed_minute, current_waiting, waiting_minutes,
                      waiting_is_more, error_code FROM shop_observations
               WHERE merchant_key = ? AND shop_id = ?
                 AND observed_minute >= ? AND observed_minute <= ?
               ORDER BY observed_minute""",
            (merchant_key, shop_id, start.isoformat(), end.isoformat()),
        ).fetchall()
        return ShopHistory(
            day=day,
            shop=ShopIdentity(
                id=int(shop[0]),
                name=str(shop[1]),
                sub_name=shop[2],
                address=shop[3],
                tel=shop[4],
                lat=shop[5],
                lng=shop[6],
            ),
            observations=[
                HistoryObservation(
                    observed_at=datetime.fromisoformat(str(row[0])),
                    current_waiting=cast(int | None, row[1]),
                    official_waiting_minutes=cast(int | None, row[2]),
                    official_waiting_is_more=bool(row[3]),
                    error_code=cast(str | None, row[4]),
                )
                for row in rows
            ],
        )

    def favorites(self) -> dict[str, list[int]]:
        rows = self._database.read(
            lambda connection: connection.execute(
                "SELECT merchant_key, shop_id FROM shop_favorites ORDER BY merchant_key, shop_id"
            ).fetchall()
        )
        result: dict[str, list[int]] = {}
        for merchant_key, shop_id in rows:
            result.setdefault(str(merchant_key), []).append(int(shop_id))
        return result

    def set_favorite(self, merchant_key: str, shop_id: int, enabled: bool) -> None:
        def update(connection: sqlite3.Connection) -> None:
            if enabled:
                connection.execute(
                    "INSERT OR IGNORE INTO shop_favorites (merchant_key, shop_id) VALUES (?, ?)",
                    (merchant_key, shop_id),
                )
            else:
                connection.execute(
                    "DELETE FROM shop_favorites WHERE merchant_key = ? AND shop_id = ?",
                    (merchant_key, shop_id),
                )

        self._database.write(update)
