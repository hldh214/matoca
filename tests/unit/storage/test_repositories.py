from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from matoca_service.matoca.models import Shop, WaitingEstimate
from matoca_service.storage.database import Database
from matoca_service.storage.models import CollectionWrite, ShopObservation, UserPreferences
from matoca_service.storage.repositories import PreferenceRepository, ShopRepository


@pytest.fixture
def database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "data" / "matoca.db")
    value.initialize()
    return value


def observation_shop(
    *,
    waiting_minutes: int | None,
    detail_fresh: bool,
    error_code: str | None = None,
) -> ShopObservation:
    return ShopObservation(
        shop=Shop(
            id=3272,
            name="Synthetic Shop",
            sub_name="Synthetic Branch",
            address="1 Test Street",
            tel="000-0000-0000",
            lat="35.0000",
            lng="138.0000",
            image_url="https://example.test/shop.jpg",
            current_waiting=12,
            is_holiday=False,
            is_suspended=False,
            waiting_time=(
                WaitingEstimate(minutes=waiting_minutes, is_more=True)
                if waiting_minutes is not None
                else None
            ),
            is_issuable=detail_fresh,
            is_open=detail_fresh,
        ),
        list_fresh=True,
        detail_fresh=detail_fresh,
        error_code=error_code,
    )


def test_save_cycle_replaces_same_minute_without_copying_stale_detail(database: Database) -> None:
    repository = ShopRepository(database)
    observed_at = datetime(2026, 9, 10, 8, 1, 40, tzinfo=UTC)
    repository.save_cycle(
        CollectionWrite(
            merchant_key="sawayaka",
            observed_at=observed_at,
            shops=[observation_shop(waiting_minutes=25, detail_fresh=True)],
        )
    )
    repository.save_cycle(
        CollectionWrite(
            merchant_key="sawayaka",
            observed_at=observed_at.replace(second=55),
            shops=[
                observation_shop(
                    waiting_minutes=None,
                    detail_fresh=False,
                    error_code="timeout",
                )
            ],
        )
    )

    rows = repository.observations("sawayaka", 3272, limit=10)

    assert len(rows) == 1
    assert rows[0].waiting_minutes is None
    assert rows[0].detail_fresh is False
    assert rows[0].is_open is None
    assert rows[0].is_issuable is None
    assert rows[0].error_code == "timeout"


def test_latest_returns_cached_shop_identity_and_latest_observation(database: Database) -> None:
    repository = ShopRepository(database)
    observed_at = datetime(2026, 9, 10, 8, 1, tzinfo=UTC)
    repository.save_cycle(
        CollectionWrite(
            merchant_key="sawayaka",
            observed_at=observed_at,
            shops=[observation_shop(waiting_minutes=25, detail_fresh=True)],
        )
    )

    shops = repository.latest("sawayaka")

    assert len(shops) == 1
    assert shops[0].shop.id == 3272
    assert shops[0].shop.name == "Synthetic Shop"
    assert shops[0].observation is not None
    assert shops[0].observation.waiting_minutes == 25
    assert shops[0].observation.observed_at == observed_at


def test_poll_window_uses_fresh_open_observations_from_preceding_thirty_days(
    database: Database,
) -> None:
    repository = ShopRepository(database)
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    repository.save_cycle(
        CollectionWrite(
            merchant_key="sawayaka",
            observed_at=now - timedelta(days=1, hours=2),
            shops=[observation_shop(waiting_minutes=25, detail_fresh=True)],
        )
    )

    window = repository.poll_window("sawayaka", now)

    assert window is not None
    assert window.start == (now - timedelta(days=1, hours=2, minutes=30)).time()
    assert window.end == (now - timedelta(days=1, hours=1, minutes=30)).time()


def test_preferences_default_to_two_adults_and_fifteen_minute_budgets(database: Database) -> None:
    preferences = PreferenceRepository(database).get()

    assert preferences.default_adult_count == 2
    assert preferences.default_child_count == 0
    assert preferences.early_tolerance_minutes == 15
    assert preferences.model_error_minutes == 15


def test_preference_update_replaces_the_global_preferences(database: Database) -> None:
    repository = PreferenceRepository(database)
    updated = repository.update(
        UserPreferences(
            default_adult_count=3,
            default_child_count=1,
            early_tolerance_minutes=10,
            model_error_minutes=20,
        )
    )

    assert updated == UserPreferences(3, 1, 10, 20)
    assert repository.get() == updated
