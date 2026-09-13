from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from matoca_service.analytics.repository import AnalyticsRepository
from matoca_service.matoca.models import Shop, WaitingEstimate
from matoca_service.storage.database import Database
from matoca_service.storage.models import CollectionWrite, ShopObservation
from matoca_service.storage.repositories import ShopRepository


@pytest.fixture
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "matoca.db")
    database.initialize()
    return database


def save(
    database: Database,
    merchant: str,
    shop_id: int,
    observed_at: datetime,
    *,
    waiting: int = 4,
    minutes: int | None = 20,
    detail_fresh: bool = True,
) -> None:
    shop = Shop(
        id=shop_id,
        name=f"Shop {shop_id}",
        address="Tokyo",
        tel="03-0000-0000",
        lat="35.1",
        lng="139.1",
        current_waiting=waiting,
        waiting_time=WaitingEstimate(minutes=minutes) if minutes is not None else None,
    )
    ShopRepository(database).save_cycle(
        CollectionWrite(
            merchant,
            observed_at,
            [
                ShopObservation(
                    shop,
                    list_fresh=True,
                    detail_fresh=detail_fresh,
                    error_code=None if detail_fresh else "timeout",
                )
            ],
        )
    )


def test_history_uses_tokyo_day_boundaries_and_preserves_missing_values(database: Database) -> None:
    save(database, "one", 7, datetime(2026, 9, 9, 14, 59, tzinfo=UTC), waiting=1)
    save(database, "one", 7, datetime(2026, 9, 9, 15, 0, tzinfo=UTC), waiting=2)
    save(
        database,
        "one",
        7,
        datetime(2026, 9, 10, 2, 0, tzinfo=UTC),
        waiting=3,
        minutes=None,
        detail_fresh=False,
    )
    save(database, "one", 7, datetime(2026, 9, 10, 14, 59, tzinfo=UTC), waiting=4)
    save(database, "one", 7, datetime(2026, 9, 10, 15, 0, tzinfo=UTC), waiting=5)
    save(database, "two", 7, datetime(2026, 9, 10, 1, 0, tzinfo=UTC), waiting=99)

    history = AnalyticsRepository(database).shop_history("one", 7, date(2026, 9, 10))

    assert history.shop.name == "Shop 7"
    assert history.shop.address == "Tokyo"
    assert history.shop.tel == "03-0000-0000"
    assert history.shop.lat == "35.1"
    assert history.shop.lng == "139.1"
    assert [item.current_waiting for item in history.observations] == [2, 3, 4]
    assert history.observations[1].official_waiting_minutes is None
    assert history.observations[1].error_code == "timeout"


def test_history_for_known_shop_with_no_samples_is_empty(database: Database) -> None:
    save(database, "one", 7, datetime(2026, 9, 10, 1, tzinfo=UTC))

    history = AnalyticsRepository(database).shop_history("one", 7, date(2026, 9, 11))

    assert history.day == date(2026, 9, 11)
    assert history.observations == []


def test_favorites_persist_and_are_isolated_by_merchant(database: Database) -> None:
    for merchant, shop_id in (("one", 7), ("two", 8), ("one", 9)):
        save(database, merchant, shop_id, datetime(2026, 9, 10, 1, tzinfo=UTC))
    repository = AnalyticsRepository(database)
    repository.set_favorite("one", 7, True)
    repository.set_favorite("two", 8, True)
    repository.set_favorite("one", 9, True)
    repository.set_favorite("one", 9, False)

    reopened = AnalyticsRepository(Database(database.path))
    assert reopened.favorites() == {"one": [7], "two": [8]}


def test_enabling_favorite_rejects_unknown_or_cross_merchant_shop(database: Database) -> None:
    save(database, "one", 7, datetime(2026, 9, 10, 1, tzinfo=UTC))

    with pytest.raises(LookupError):
        AnalyticsRepository(database).set_favorite("two", 7, True)

    assert AnalyticsRepository(database).favorites() == {}
