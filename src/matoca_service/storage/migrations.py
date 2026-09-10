import sqlite3
from collections.abc import Callable

Migration = Callable[[sqlite3.Connection], None]


def _bootstrap_metadata(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE database_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _create_business_storage(connection: sqlite3.Connection) -> None:
    statements = (
        """
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
        )
        """,
        """

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
        )
        """,
        """

        CREATE INDEX shop_observations_history
        ON shop_observations (merchant_key, shop_id, observed_minute DESC)
        """,
        """

        CREATE TABLE merchant_poll_state (
            merchant_key TEXT PRIMARY KEY,
            last_attempt_at TEXT,
            last_success_at TEXT,
            retry_at TEXT,
            error_code TEXT
        )
        """,
        """

        CREATE TABLE preferences (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            default_adult_count INTEGER NOT NULL,
            default_child_count INTEGER NOT NULL,
            early_tolerance_minutes INTEGER NOT NULL,
            model_error_minutes INTEGER NOT NULL
        )
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _add_poll_failure_count(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        ALTER TABLE merchant_poll_state
        ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0)
        """
    )


def _create_observation_rollups(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE shop_observation_rollups_5m (
            merchant_key TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            observed_5_minute TEXT NOT NULL,
            sample_count INTEGER NOT NULL,
            minimum_waiting INTEGER,
            maximum_waiting INTEGER,
            average_waiting REAL,
            waiting_minutes_sample_count INTEGER NOT NULL,
            minimum_waiting_minutes INTEGER,
            maximum_waiting_minutes INTEGER,
            average_waiting_minutes REAL,
            PRIMARY KEY (merchant_key, shop_id, observed_5_minute),
            FOREIGN KEY (merchant_key, shop_id)
                REFERENCES shops (merchant_key, shop_id)
        )
        """,
        """
        CREATE INDEX shop_observation_rollups_history
        ON shop_observation_rollups_5m (merchant_key, shop_id, observed_5_minute DESC)
        """,
    )
    for statement in statements:
        connection.execute(statement)


MIGRATIONS: tuple[Migration, ...] = (
    _bootstrap_metadata,
    _create_business_storage,
    _add_poll_failure_count,
    _create_observation_rollups,
)


def migrate(connection: sqlite3.Connection) -> None:
    current_version = connection.execute("PRAGMA user_version").fetchone()[0]

    for version, migration in enumerate(MIGRATIONS, start=1):
        if version <= current_version:
            continue
        connection.execute("BEGIN")
        try:
            migration(connection)
            connection.execute(f"PRAGMA user_version = {version}")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")
