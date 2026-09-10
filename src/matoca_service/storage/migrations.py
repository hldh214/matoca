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


MIGRATIONS: tuple[Migration, ...] = (_bootstrap_metadata,)


def migrate(connection: sqlite3.Connection) -> None:
    current_version = connection.execute("PRAGMA user_version").fetchone()[0]

    for version, migration in enumerate(MIGRATIONS, start=1):
        if version <= current_version:
            continue
        with connection:
            migration(connection)
            connection.execute(f"PRAGMA user_version = {version}")
