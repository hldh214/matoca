from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from matoca_service.analytics.repository import AnalyticsRepository
from matoca_service.automation.replay import ReplayRequest, replay
from matoca_service.storage.database import Database

START = datetime(2026, 9, 14, 1, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "synthetic.db")
    db.initialize()
    return db


def seed(db: Database, *, start=START, count=20, shop=1, drop=10):
    def write(connection):
        connection.execute(
            "INSERT OR IGNORE INTO shops (merchant_key, shop_id, name, options_json) "
            "VALUES ('one', ?, 'test', '{}')",
            (shop,),
        )
        connection.executemany(
            """INSERT INTO shop_observations
            (merchant_key, shop_id, observed_minute, current_waiting, waiting_minutes,
             waiting_is_more, is_open, is_issuable, is_holiday, is_suspended,
             list_fresh, detail_fresh, error_code)
            VALUES ('one', ?, ?, 4, ?, 0, 1, 1, 0, 0, 1, 1, NULL)""",
            [
                (
                    shop,
                    (start + timedelta(minutes=6 * i + j)).isoformat(),
                    40 - drop if j == 4 else 40,
                )
                for i in range(count)
                for j in range(5)
            ],
        )

    db.write(write)


@pytest.mark.parametrize(
    "column,value",
    [
        ("list_fresh", 0),
        ("detail_fresh", 0),
        ("error_code", "timeout"),
        ("waiting_is_more", 1),
        ("waiting_minutes", None),
        ("waiting_minutes", -1),
        ("waiting_minutes", 0),
        ("is_open", 0),
        ("is_open", None),
        ("is_issuable", 0),
        ("is_holiday", 1),
        ("is_suspended", 1),
    ],
)
def test_repository_keeps_invalid_intermediate_rows(database, column, value):
    seed(database, count=1)
    database.write(
        lambda connection: connection.execute(
            f"UPDATE shop_observations SET {column}=? WHERE observed_minute=?",
            (value, (START + timedelta(minutes=2)).isoformat()),
        )
    )
    summary = AnalyticsRepository(database).trend_summary("one", 1, START + timedelta(minutes=5))
    assert summary.sample_count == 0
    assert summary.suggested_addition_minutes is None


def test_repository_as_of_and_merchant_isolation(database):
    seed(database)
    repository = AnalyticsRepository(database)
    assert repository.trend_summary("one", 1, START + timedelta(minutes=117)).sample_count == 19
    assert (
        repository.trend_summary(
            "one", 1, START + timedelta(minutes=118)
        ).suggested_addition_minutes
        == 10
    )
    with pytest.raises(LookupError):
        repository.trend_summary("another", 1, START + timedelta(minutes=118))


def test_replay_comparison_is_past_only_clamped_and_loads_trends_once(database, monkeypatch):
    seed(database, start=START - timedelta(days=1))
    seed(database, start=START, count=1)
    request = ReplayRequest(
        merchant_key="one",
        shop_id=1,
        day=START.date(),
        arrival_at=START + timedelta(minutes=50),
        model_error_minutes=115,
    )
    calls = []
    original = AnalyticsRepository.trend_timeline

    def tracked(self, *args, **kwargs):
        calls.append(args)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AnalyticsRepository, "trend_timeline", tracked)
    before = replay(database, request, as_of=START + timedelta(hours=1))
    assert len(calls) == 1
    assert before.comparison[0].baseline_margin_minutes == 115
    assert before.comparison[0].suggested_addition_minutes == 10
    assert before.comparison[0].suggested_margin_minutes == 120
    seed(database, start=START + timedelta(minutes=10), count=20, drop=35)
    after = replay(database, request, as_of=START + timedelta(hours=3))
    assert before.comparison == after.comparison[:5]
    assert after.comparison[-1].suggested_addition_minutes == 35
    assert before.timing_only is True
    assert (
        database.read(lambda c: c.execute("SELECT count(*) FROM queue_sessions").fetchone()[0]) == 0
    )
