import os
import sqlite3
import stat
from pathlib import Path

import pytest

from matoca_service.storage.database import Database
from matoca_service.storage.migrations import MIGRATIONS, migrate
from matoca_service.storage.models import MerchantPollState
from matoca_service.storage.repositories import ShopRepository


def test_initialize_creates_private_database_and_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "matoca.db")

    database.initialize()

    assert stat.S_IMODE(database.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.path.stat().st_mode) == 0o600
    assert (
        database.read(lambda connection: connection.execute("PRAGMA user_version").fetchone()[0])
        == 5
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

        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'shops'"
        ).fetchone() == ("shops",)


def test_initialize_upgrades_exact_pre_fix_version_two_database(tmp_path: Path) -> None:
    path = tmp_path / "data" / "matoca.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE database_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE shops (
                merchant_key TEXT NOT NULL,
                shop_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                sub_name TEXT,
                address TEXT,
                tel TEXT,
                lat TEXT,
                lng TEXT,
                image_url TEXT,
                forms_json TEXT,
                options_json TEXT NOT NULL,
                last_detail_at TEXT,
                PRIMARY KEY (merchant_key, shop_id)
            );
            CREATE TABLE shop_observations (
                merchant_key TEXT NOT NULL,
                shop_id INTEGER NOT NULL,
                observed_minute TEXT NOT NULL,
                current_waiting INTEGER NOT NULL,
                waiting_minutes INTEGER,
                waiting_is_more INTEGER NOT NULL,
                is_open INTEGER,
                is_issuable INTEGER,
                is_holiday INTEGER NOT NULL,
                is_suspended INTEGER NOT NULL,
                list_fresh INTEGER NOT NULL,
                detail_fresh INTEGER NOT NULL,
                error_code TEXT,
                PRIMARY KEY (merchant_key, shop_id, observed_minute),
                FOREIGN KEY (merchant_key, shop_id)
                    REFERENCES shops (merchant_key, shop_id)
            );
            CREATE INDEX shop_observations_history
            ON shop_observations (merchant_key, shop_id, observed_minute DESC);
            CREATE TABLE merchant_poll_state (
                merchant_key TEXT PRIMARY KEY,
                last_attempt_at TEXT,
                last_success_at TEXT,
                retry_at TEXT,
                error_code TEXT
            );
            CREATE TABLE preferences (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                default_adult_count INTEGER NOT NULL,
                default_child_count INTEGER NOT NULL,
                early_tolerance_minutes INTEGER NOT NULL,
                model_error_minutes INTEGER NOT NULL
            );
            PRAGMA user_version = 2;
            """
        )

    database = Database(path)
    database.initialize()

    assert (
        database.read(lambda connection: connection.execute("PRAGMA user_version").fetchone()[0])
        == 5
    )
    column = database.read(
        lambda connection: connection.execute(
            "SELECT dflt_value FROM pragma_table_info('merchant_poll_state') "
            "WHERE name = 'failure_count'"
        ).fetchone()
    )
    assert column == ("0",)

    repository = ShopRepository(database)
    assert repository.poll_state("sawayaka").failure_count == 0
    state = MerchantPollState(merchant_key="sawayaka", failure_count=2)
    repository.update_poll_state(state)
    assert repository.poll_state("sawayaka") == state


def test_initialize_upgrades_exact_version_three_database(tmp_path: Path) -> None:
    path = tmp_path / "data" / "matoca.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as connection:
        for migration in MIGRATIONS[:3]:
            migration(connection)
        connection.execute("PRAGMA user_version = 3")

    database = Database(path)
    database.initialize()

    assert (
        database.read(lambda connection: connection.execute("PRAGMA user_version").fetchone()[0])
        == 5
    )
    assert database.read(
        lambda connection: connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'shop_observation_rollups_5m'"
        ).fetchone()
    ) == ("shop_observation_rollups_5m",)


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_additive_catalog_upgrade_preserves_legacy_data(tmp_path: Path, version: int) -> None:
    path = tmp_path / "data" / "matoca.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as connection:
        for migration in MIGRATIONS[:version]:
            migration(connection)
        connection.execute(f"PRAGMA user_version = {version}")
        connection.execute("INSERT INTO database_metadata VALUES ('synthetic', 'preserved')")
        if version >= 2:
            connection.execute(
                "INSERT INTO shops (merchant_key, shop_id, name, options_json) "
                "VALUES ('test', 1, 'Original', '{}')"
            )
            connection.execute("""INSERT INTO shop_observations VALUES
                ('test', 1, '2026-09-10T00:00:00+00:00', 7, 15, 0, 1, 1, 0, 0, 1, 1, NULL)""")
            connection.execute("INSERT INTO preferences VALUES (1, 3, 1, 10, 20)")
    database = Database(path)
    database.initialize()
    assert (
        database.read(lambda connection: connection.execute("PRAGMA user_version").fetchone()[0])
        == 5
    )
    assert database.read(
        lambda connection: connection.execute(
            "SELECT value FROM database_metadata WHERE key = 'synthetic'"
        ).fetchone()
    ) == ("preserved",)
    if version >= 2:
        repository = ShopRepository(database)
        assert repository.latest("test")[0].shop.current_waiting == 7
        assert repository.catalog_state("test").complete is False
        assert database.read(
            lambda connection: connection.execute(
                "SELECT default_adult_count FROM preferences"
            ).fetchone()
        ) == (3,)
