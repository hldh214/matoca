import os
import sqlite3
import stat
from pathlib import Path

import pytest

from matoca_service.storage.database import Database
from matoca_service.storage.migrations import migrate


def test_initialize_creates_private_database_and_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "matoca.db")

    database.initialize()

    assert stat.S_IMODE(database.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.path.stat().st_mode) == 0o600
    assert (
        database.read(lambda connection: connection.execute("PRAGMA user_version").fetchone()[0])
        == 2
    )


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "matoca.db")
    database.initialize()
    database.initialize()

    journal_mode = database.read(
        lambda connection: connection.execute("PRAGMA journal_mode").fetchone()[0]
    )

    assert journal_mode == "wal"


def test_initialize_hardens_database_after_failed_migration(tmp_path: Path) -> None:
    path = tmp_path / "data" / "matoca.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE database_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
    os.chmod(path, 0o644)

    with pytest.raises(sqlite3.OperationalError):
        Database(path).initialize()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_business_migration_rolls_back_schema_and_version_after_ddl_failure(tmp_path: Path) -> None:
    path = tmp_path / "matoca.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE database_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("PRAGMA user_version = 1")

        def deny_observation_table(
            action: int,
            argument_one: str | None,
            _argument_two: str | None,
            _database: str | None,
            _source: str | None,
        ) -> int:
            if action == sqlite3.SQLITE_CREATE_TABLE and argument_one == "shop_observations":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_observation_table)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            migrate(connection)

        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        assert tables == [("database_metadata",)]
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1

        connection.set_authorizer(None)
        migrate(connection)

        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'shops'"
        ).fetchone() == ("shops",)
