from datetime import UTC, datetime, timedelta
from pathlib import Path

from matoca_service.prediction.repository import PredictionRepository
from matoca_service.storage.database import Database

NOW = datetime(2026, 9, 14, tzinfo=UTC)


def test_repository_returns_only_exact_confirmed_known_start_samples(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "matoca.sqlite3")
    database.initialize()

    def seed(connection):
        rows = [
            ("manual", 20, 0, NOW, NOW + timedelta(minutes=30), "called"),
            ("automation", 20, 0, NOW, NOW, "called"),
            ("manual", 20, 1, NOW, NOW + timedelta(minutes=30), "called"),
            ("adopted", 20, 0, NOW, NOW + timedelta(minutes=30), "called"),
            ("manual", 0, 0, NOW, NOW + timedelta(minutes=30), "called"),
            ("manual", 20, 0, NOW, NOW - timedelta(minutes=1), "called"),
            ("manual", 20, 0, NOW, None, "unknown"),
            ("manual", 20, 0, NOW, None, "cancelled"),
        ]
        for index, (source, official, is_more, submitted, called, status) in enumerate(rows):
            cursor = connection.execute(
                """INSERT INTO queue_sessions
                (merchant_key, shop_id, waiting_id, source, submitted_at, first_observed_at,
                 official_minutes_at_submission, official_is_more_at_submission, called_at, status)
                VALUES ('merchant', 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    index + 1,
                    source,
                    submitted.isoformat(),
                    submitted.isoformat(),
                    official,
                    is_more,
                    called.isoformat() if called else None,
                    status,
                ),
            )
            if index in {0, 1}:
                connection.execute(
                    """INSERT INTO queue_milestones
                       VALUES (?, 'calling', ?, ?, 'api_observation', 60)""",
                    (cursor.lastrowid, called.isoformat(), called.isoformat()),
                )
                connection.execute(
                    """INSERT INTO queue_session_observations
                       (session_id, observed_minute, count) VALUES (?, ?, 0)""",
                    (cursor.lastrowid, called.isoformat()),
                )

    database.write(seed)

    samples = PredictionRepository(database).samples("merchant", NOW + timedelta(days=1))

    assert [item.ratio for item in samples] == [1.5, 0.0]


def test_repository_does_not_leak_future_call_labels_into_historical_predictions(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "data" / "matoca.sqlite3")
    database.initialize()

    def seed(connection):
        cursor = connection.execute(
            """INSERT INTO queue_sessions
            (merchant_key, shop_id, waiting_id, source, submitted_at, first_observed_at,
             official_minutes_at_submission, official_is_more_at_submission, called_at, status)
            VALUES ('merchant', 1, 1, 'manual', ?, ?, 20, 0, ?, 'called')""",
            (NOW.isoformat(), NOW.isoformat(), (NOW + timedelta(minutes=30)).isoformat()),
        )
        connection.execute(
            """INSERT INTO queue_session_observations
               (session_id, observed_minute, count) VALUES (?, ?, 0)""",
            (cursor.lastrowid, (NOW + timedelta(minutes=30)).isoformat()),
        )
        connection.execute(
            "INSERT INTO queue_milestones VALUES (?, 'calling', ?, ?, 'api_observation', 60)",
            (
                cursor.lastrowid,
                (NOW + timedelta(minutes=30)).isoformat(),
                (NOW + timedelta(minutes=30)).isoformat(),
            ),
        )

    database.write(seed)

    assert PredictionRepository(database).samples("merchant", NOW + timedelta(minutes=15)) == []


def test_repository_excludes_called_status_without_explicit_evidence(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "matoca.sqlite3")
    database.initialize()

    def seed(connection):
        connection.execute(
            """INSERT INTO queue_sessions
            (merchant_key, shop_id, waiting_id, source, submitted_at, first_observed_at,
             official_minutes_at_submission, official_is_more_at_submission, called_at, status)
            VALUES ('merchant', 1, 1, 'manual', ?, ?, 20, 0, ?, 'called')""",
            (NOW.isoformat(), NOW.isoformat(), (NOW + timedelta(minutes=30)).isoformat()),
        )

    database.write(seed)

    assert PredictionRepository(database).samples("merchant", NOW + timedelta(hours=1)) == []
