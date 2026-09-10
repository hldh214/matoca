from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from matoca_service.matoca.models import Shop, WaitingEstimate
from matoca_service.storage.database import Database
from matoca_service.storage.models import CollectionWrite, ShopObservation
from matoca_service.storage.repositories import ShopRepository

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "data" / "matoca.db")
    value.initialize()
    return value


def cycle_at(
    observed_at: datetime,
    *,
    waiting: int,
    waiting_minutes: int | None = 25,
    list_fresh: bool = True,
    detail_fresh: bool = True,
) -> CollectionWrite:
    return CollectionWrite(
        merchant_key="sawayaka",
        observed_at=observed_at,
        shops=[
            ShopObservation(
                shop=Shop(
                    id=3272,
                    name="Synthetic Shop",
                    current_waiting=waiting,
                    waiting_time=(
                        WaitingEstimate(minutes=waiting_minutes, is_more=False)
                        if waiting_minutes is not None
                        else None
                    ),
                    is_open=detail_fresh,
                    is_issuable=detail_fresh,
                ),
                list_fresh=list_fresh,
                detail_fresh=detail_fresh,
            )
        ],
    )


def test_rollup_commits_before_raw_rows_are_deleted(database: Database) -> None:
    repository = ShopRepository(database)
    repository.save_cycle(cycle_at(NOW - timedelta(days=181), waiting=10))
    repository.save_cycle(cycle_at(NOW - timedelta(days=179), waiting=8))

    result = repository.rollup_and_prune(NOW)

    assert result.raw_deleted == 1
    assert repository.rollups("sawayaka", 3272)[0].sample_count == 1
    assert len(repository.observations("sawayaka", 3272, limit=10)) == 1


def test_rollup_aggregates_only_fresh_metrics(database: Database) -> None:
    repository = ShopRepository(database)
    observed_at = NOW - timedelta(days=181)
    repository.save_cycle(cycle_at(observed_at, waiting=10, waiting_minutes=25))
    repository.save_cycle(
        cycle_at(
            observed_at + timedelta(minutes=1),
            waiting=99,
            waiting_minutes=99,
            list_fresh=False,
            detail_fresh=False,
        )
    )

    repository.rollup_and_prune(NOW)

    rollup = repository.rollups("sawayaka", 3272)[0]
    assert rollup.sample_count == 1
    assert rollup.minimum_waiting == 10
    assert rollup.maximum_waiting == 10
    assert rollup.average_waiting == 10.0
    assert rollup.waiting_minutes_sample_count == 1
    assert rollup.minimum_waiting_minutes == 25
    assert rollup.maximum_waiting_minutes == 25
    assert rollup.average_waiting_minutes == 25.0


def test_rollup_runs_at_most_once_per_tokyo_day(database: Database) -> None:
    repository = ShopRepository(database)
    repository.save_cycle(cycle_at(NOW - timedelta(days=181), waiting=10))
    repository.rollup_and_prune(NOW)
    repository.save_cycle(cycle_at(NOW - timedelta(days=181, minutes=-1), waiting=11))

    result = repository.rollup_and_prune(NOW + timedelta(hours=1))

    assert result.raw_deleted == 0
    assert len(repository.observations("sawayaka", 3272, limit=10)) == 1
