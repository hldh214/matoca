import json
import sqlite3
from datetime import UTC, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from matoca_service.matoca.models import Shop, ShopForms, ShopOptions, WaitingEstimate
from matoca_service.storage.database import Database
from matoca_service.storage.models import (
    CollectionWrite,
    MerchantPollState,
    PollWindow,
    ShopObservation,
    StoredShop,
    UserPreferences,
)

TOKYO = ZoneInfo("Asia/Tokyo")


class ShopRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def save_cycle(self, cycle: CollectionWrite) -> None:
        self._database.write(lambda connection: self._save_cycle(connection, cycle))

    def latest(self, merchant_key: str) -> list[StoredShop]:
        return self._database.read(lambda connection: self._latest(connection, merchant_key))

    def observations(self, merchant_key: str, shop_id: int, *, limit: int) -> list[ShopObservation]:
        return self._database.read(
            lambda connection: self._observations(connection, merchant_key, shop_id, limit)
        )

    def poll_window(self, merchant_key: str, now: datetime) -> PollWindow | None:
        return self._database.read(
            lambda connection: self._poll_window(connection, merchant_key, now)
        )

    def poll_state(self, merchant_key: str) -> MerchantPollState:
        return self._database.read(lambda connection: self._poll_state(connection, merchant_key))

    def update_poll_state(self, state: MerchantPollState) -> MerchantPollState:
        self._database.write(lambda connection: self._update_poll_state(connection, state))
        return state

    def _save_cycle(self, connection: sqlite3.Connection, cycle: CollectionWrite) -> None:
        observed_minute = _normalize_minute(cycle.observed_at)
        for observation in cycle.shops:
            shop = observation.shop
            forms_json = _canonical_json(shop.forms)
            options_json = _canonical_json(shop.options)
            connection.execute(
                """
                INSERT INTO shops (
                    merchant_key, shop_id, name, sub_name, address, tel, lat, lng, image_url,
                    forms_json, options_json, last_detail_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (merchant_key, shop_id) DO UPDATE SET
                    name = excluded.name,
                    sub_name = excluded.sub_name,
                    address = excluded.address,
                    tel = excluded.tel,
                    lat = excluded.lat,
                    lng = excluded.lng,
                    image_url = excluded.image_url,
                    forms_json = CASE WHEN excluded.last_detail_at IS NOT NULL
                        THEN excluded.forms_json ELSE shops.forms_json END,
                    options_json = CASE WHEN excluded.last_detail_at IS NOT NULL
                        THEN excluded.options_json ELSE shops.options_json END,
                    last_detail_at = COALESCE(excluded.last_detail_at, shops.last_detail_at)
                """,
                (
                    cycle.merchant_key,
                    shop.id,
                    shop.name,
                    shop.sub_name,
                    shop.address,
                    shop.tel,
                    str(shop.lat) if shop.lat is not None else None,
                    str(shop.lng) if shop.lng is not None else None,
                    shop.image_url,
                    forms_json,
                    options_json,
                    _serialize_datetime(observed_minute) if observation.detail_fresh else None,
                ),
            )
            connection.execute(
                """
                INSERT INTO shop_observations (
                    merchant_key, shop_id, observed_minute, current_waiting, waiting_minutes,
                    waiting_is_more, is_open, is_issuable, is_holiday, is_suspended, list_fresh,
                    detail_fresh, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (merchant_key, shop_id, observed_minute) DO UPDATE SET
                    current_waiting = excluded.current_waiting,
                    waiting_minutes = excluded.waiting_minutes,
                    waiting_is_more = excluded.waiting_is_more,
                    is_open = excluded.is_open,
                    is_issuable = excluded.is_issuable,
                    is_holiday = excluded.is_holiday,
                    is_suspended = excluded.is_suspended,
                    list_fresh = excluded.list_fresh,
                    detail_fresh = excluded.detail_fresh,
                    error_code = excluded.error_code
                """,
                (
                    cycle.merchant_key,
                    shop.id,
                    _serialize_datetime(observed_minute),
                    observation.current_waiting,
                    observation.waiting_minutes,
                    int(observation.waiting_is_more),
                    _sqlite_bool(observation.is_open),
                    _sqlite_bool(observation.is_issuable),
                    int(observation.is_holiday),
                    int(observation.is_suspended),
                    int(observation.list_fresh),
                    int(observation.detail_fresh),
                    observation.error_code,
                ),
            )

    def _latest(self, connection: sqlite3.Connection, merchant_key: str) -> list[StoredShop]:
        rows = connection.execute(
            """
            SELECT s.*, o.observed_minute, o.current_waiting, o.waiting_minutes, o.waiting_is_more,
                   o.is_open, o.is_issuable, o.is_holiday, o.is_suspended, o.list_fresh,
                   o.detail_fresh, o.error_code
            FROM shops AS s
            LEFT JOIN shop_observations AS o
              ON o.merchant_key = s.merchant_key AND o.shop_id = s.shop_id
             AND o.observed_minute = (
                SELECT MAX(latest.observed_minute)
                FROM shop_observations AS latest
                WHERE latest.merchant_key = s.merchant_key AND latest.shop_id = s.shop_id
             )
            WHERE s.merchant_key = ?
            ORDER BY s.name, s.shop_id
            """,
            (merchant_key,),
        ).fetchall()
        return [self._stored_shop_from_row(row) for row in rows]

    def _observations(
        self,
        connection: sqlite3.Connection,
        merchant_key: str,
        shop_id: int,
        limit: int,
    ) -> list[ShopObservation]:
        rows = connection.execute(
            """
            SELECT s.*, o.observed_minute, o.current_waiting, o.waiting_minutes, o.waiting_is_more,
                   o.is_open, o.is_issuable, o.is_holiday, o.is_suspended, o.list_fresh,
                   o.detail_fresh, o.error_code
            FROM shop_observations AS o
            JOIN shops AS s ON s.merchant_key = o.merchant_key AND s.shop_id = o.shop_id
            WHERE o.merchant_key = ? AND o.shop_id = ?
            ORDER BY o.observed_minute DESC
            LIMIT ?
            """,
            (merchant_key, shop_id, limit),
        ).fetchall()
        return [self._observation_from_row(row) for row in rows]

    def _poll_window(
        self,
        connection: sqlite3.Connection,
        merchant_key: str,
        now: datetime,
    ) -> PollWindow | None:
        local_now = _as_utc(now).astimezone(TOKYO)
        first_day = local_now.date() - timedelta(days=29)
        first_minute = datetime.combine(first_day, time.min, tzinfo=TOKYO)
        next_day = datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=TOKYO)
        rows = connection.execute(
            """
            SELECT observed_minute
            FROM shop_observations
            WHERE merchant_key = ? AND observed_minute >= ? AND observed_minute < ?
              AND detail_fresh = 1 AND is_open = 1
            """,
            (
                merchant_key,
                _serialize_datetime(first_minute),
                _serialize_datetime(next_day),
            ),
        ).fetchall()
        if not rows:
            return None

        local_minutes = [
            _minute_of_day(_parse_datetime(str(row[0])).astimezone(TOKYO)) for row in rows
        ]
        start_minute = min(local_minutes) - 30
        end_minute = max(local_minutes) + 30
        return PollWindow(
            start=_time_from_minute(start_minute),
            end=_time_from_minute(end_minute),
            crosses_midnight=start_minute < 0 or end_minute >= 24 * 60,
        )

    def _poll_state(self, connection: sqlite3.Connection, merchant_key: str) -> MerchantPollState:
        row = connection.execute(
            """
            SELECT merchant_key, last_attempt_at, last_success_at, retry_at,
                   error_code, failure_count
            FROM merchant_poll_state WHERE merchant_key = ?
            """,
            (merchant_key,),
        ).fetchone()
        if row is None:
            return MerchantPollState(merchant_key=merchant_key)
        return MerchantPollState(
            merchant_key=row[0],
            last_attempt_at=_parse_optional_datetime(row[1]),
            last_success_at=_parse_optional_datetime(row[2]),
            retry_at=_parse_optional_datetime(row[3]),
            error_code=row[4],
            failure_count=int(cast(int | str, row[5])),
        )

    def _update_poll_state(self, connection: sqlite3.Connection, state: MerchantPollState) -> None:
        connection.execute(
            """
            INSERT INTO merchant_poll_state (
                merchant_key, last_attempt_at, last_success_at, retry_at, error_code, failure_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (merchant_key) DO UPDATE SET
                last_attempt_at = excluded.last_attempt_at,
                last_success_at = excluded.last_success_at,
                retry_at = excluded.retry_at,
                error_code = excluded.error_code,
                failure_count = excluded.failure_count
            """,
            (
                state.merchant_key,
                _serialize_optional_datetime(state.last_attempt_at),
                _serialize_optional_datetime(state.last_success_at),
                _serialize_optional_datetime(state.retry_at),
                state.error_code,
                state.failure_count,
            ),
        )

    def _stored_shop_from_row(self, row: tuple[object, ...]) -> StoredShop:
        shop = _shop_from_row(row)
        observation = self._observation_from_row(row) if row[12] is not None else None
        return StoredShop(
            merchant_key=str(row[0]),
            shop=shop,
            observation=observation,
            last_detail_at=_parse_optional_datetime(row[11]),
        )

    def _observation_from_row(self, row: tuple[object, ...]) -> ShopObservation:
        shop = _shop_from_row(row)
        return ShopObservation(
            shop=shop,
            list_fresh=bool(row[20]),
            detail_fresh=bool(row[21]),
            error_code=_optional_string(row[22]),
            observed_at=_parse_datetime(str(row[12])),
        )


class PreferenceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get(self) -> UserPreferences:
        return self._database.read(self._get)

    def update(self, preferences: UserPreferences) -> UserPreferences:
        self._database.write(lambda connection: self._update(connection, preferences))
        return preferences

    def _get(self, connection: sqlite3.Connection) -> UserPreferences:
        row = connection.execute(
            """
            SELECT default_adult_count, default_child_count, early_tolerance_minutes,
                   model_error_minutes
            FROM preferences WHERE singleton = 1
            """
        ).fetchone()
        if row is None:
            return UserPreferences()
        return UserPreferences(*row)

    def _update(self, connection: sqlite3.Connection, preferences: UserPreferences) -> None:
        connection.execute(
            """
            INSERT INTO preferences (
                singleton, default_adult_count, default_child_count, early_tolerance_minutes,
                model_error_minutes
            ) VALUES (1, ?, ?, ?, ?)
            ON CONFLICT (singleton) DO UPDATE SET
                default_adult_count = excluded.default_adult_count,
                default_child_count = excluded.default_child_count,
                early_tolerance_minutes = excluded.early_tolerance_minutes,
                model_error_minutes = excluded.model_error_minutes
            """,
            (
                preferences.default_adult_count,
                preferences.default_child_count,
                preferences.early_tolerance_minutes,
                preferences.model_error_minutes,
            ),
        )


def _shop_from_row(row: tuple[object, ...]) -> Shop:
    forms_json = _optional_string(row[9])
    options_json = str(row[10])
    waiting_minutes = row[14]
    detail_fresh = bool(row[21]) if len(row) > 21 else False
    return Shop(
        id=int(cast(int | str, row[1])),
        name=str(row[2]),
        sub_name=_optional_string(row[3]),
        address=_optional_string(row[4]),
        tel=_optional_string(row[5]),
        lat=_optional_string(row[6]),
        lng=_optional_string(row[7]),
        image_url=_optional_string(row[8]),
        forms=ShopForms.model_validate_json(forms_json) if forms_json is not None else None,
        options=ShopOptions.model_validate_json(options_json),
        current_waiting=int(cast(int | str, row[13])) if row[13] is not None else 0,
        waiting_time=(
            WaitingEstimate(minutes=int(cast(int | str, waiting_minutes)), is_more=bool(row[15]))
            if detail_fresh and waiting_minutes is not None
            else None
        ),
        is_open=bool(row[16]) if detail_fresh and row[16] is not None else False,
        is_issuable=bool(row[17]) if detail_fresh and row[17] is not None else False,
        is_holiday=bool(row[18]) if row[18] is not None else False,
        is_suspended=bool(row[19]) if row[19] is not None else False,
    )


def _canonical_json(value: ShopForms | ShopOptions | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _normalize_minute(value: datetime) -> datetime:
    return _as_utc(value).replace(second=0, microsecond=0)


def _serialize_datetime(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _serialize_optional_datetime(value: datetime | None) -> str | None:
    return _serialize_datetime(value) if value is not None else None


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _parse_optional_datetime(value: object) -> datetime | None:
    return _parse_datetime(str(value)) if value is not None else None


def _sqlite_bool(value: bool | None) -> int | None:
    return int(value) if value is not None else None


def _minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _time_from_minute(value: int) -> time:
    hour, minute = divmod(value % (24 * 60), 60)
    return time(hour, minute)


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None
