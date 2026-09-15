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


def _create_catalog_state(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE merchant_catalog_state (
            merchant_key TEXT PRIMARY KEY,
            observed_at TEXT NOT NULL,
            complete INTEGER NOT NULL,
            static_refreshed_at TEXT
        )
    """)
    connection.execute("""
        CREATE TABLE catalog_members (
            merchant_key TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            PRIMARY KEY (merchant_key, shop_id),
            FOREIGN KEY (merchant_key, shop_id) REFERENCES shops (merchant_key, shop_id)
        )
    """)
    # Old schemas did not record complete list membership. Expose only the newest
    # observed set, marked stale until the next complete catalog establishes it.
    connection.execute("""
        INSERT INTO merchant_catalog_state (merchant_key, observed_at, complete)
        SELECT merchant_key, MAX(observed_minute), 0 FROM shop_observations
        GROUP BY merchant_key
    """)
    connection.execute("""
        INSERT INTO catalog_members
        SELECT o.merchant_key, o.shop_id FROM shop_observations o
        JOIN merchant_catalog_state c
          ON c.merchant_key = o.merchant_key AND c.observed_at = o.observed_minute
    """)
    connection.execute("""
        CREATE INDEX shop_observations_poll_window
        ON shop_observations (merchant_key, observed_minute)
        WHERE detail_fresh = 1 AND is_open = 1
    """)


def _create_shop_favorites(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE shop_favorites (
            merchant_key TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            PRIMARY KEY (merchant_key, shop_id)
        )
    """)


def _create_queue_tracking(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE queue_intents (
            intent_id TEXT PRIMARY KEY,
            merchant_key TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            submitted_at TEXT NOT NULL,
            official_minutes_at_submission INTEGER,
            official_is_more_at_submission INTEGER,
            adult_count INTEGER NOT NULL,
            child_count INTEGER NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('manual', 'automation')),
            status TEXT NOT NULL CHECK (status IN ('pending', 'resolved', 'unresolved', 'failed')),
            waiting_id INTEGER,
            error_code TEXT
        )
        """,
        """
        CREATE UNIQUE INDEX queue_intents_unfinished_account
        ON queue_intents ((1)) WHERE status IN ('pending', 'unresolved')
        """,
        """
        CREATE TABLE queue_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            intent_id TEXT UNIQUE,
            merchant_key TEXT NOT NULL,
            shop_id INTEGER,
            waiting_id INTEGER NOT NULL,
            number INTEGER,
            adult_count INTEGER,
            child_count INTEGER,
            source TEXT NOT NULL CHECK (source IN ('manual', 'automation', 'adopted')),
            submitted_at TEXT,
            first_observed_at TEXT NOT NULL,
            official_minutes_at_submission INTEGER,
            official_is_more_at_submission INTEGER,
            called_at TEXT,
            cancelled_at TEXT,
            terminal_at TEXT,
            status TEXT NOT NULL CHECK (status IN ('active', 'called', 'cancelled', 'unknown')),
            cancellation_requested_at TEXT,
            UNIQUE (merchant_key, waiting_id),
            FOREIGN KEY (intent_id) REFERENCES queue_intents (intent_id)
        )
        """,
        """
        CREATE TABLE queue_session_observations (
            session_id INTEGER NOT NULL,
            observed_minute TEXT NOT NULL,
            count INTEGER,
            PRIMARY KEY (session_id, observed_minute),
            FOREIGN KEY (session_id) REFERENCES queue_sessions (session_id)
        )
        """,
        """
        CREATE TABLE queue_tracking_state (
            merchant_key TEXT PRIMARY KEY,
            last_attempt_at TEXT NOT NULL,
            last_success_at TEXT,
            error_code TEXT
        )
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _create_automation(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE automation_tasks (
        id TEXT PRIMARY KEY, merchant_key TEXT NOT NULL, payload TEXT NOT NULL,
        form_signature TEXT NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL,
        intent_id TEXT UNIQUE REFERENCES queue_intents(intent_id), last_decision TEXT NOT NULL,
        evaluated_at TEXT, next_evaluation_at TEXT)""")
    connection.execute("""CREATE TABLE automation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES automation_tasks(id),
        at TEXT NOT NULL, state TEXT NOT NULL, decision TEXT NOT NULL)""")


MIGRATIONS: tuple[Migration, ...] = (
    _bootstrap_metadata,
    _create_business_storage,
    _add_poll_failure_count,
    _create_observation_rollups,
    _create_catalog_state,
    _create_shop_favorites,
    _create_queue_tracking,
    _create_automation,
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
