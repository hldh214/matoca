import stat
from pathlib import Path

from matoca_service.storage.database import Database


def test_initialize_creates_private_database_and_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "matoca.db")

    database.initialize()

    assert stat.S_IMODE(database.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.path.stat().st_mode) == 0o600
    assert (
        database.read(lambda connection: connection.execute("PRAGMA user_version").fetchone()[0])
        == 1
    )


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "matoca.db")
    database.initialize()
    database.initialize()

    journal_mode = database.read(
        lambda connection: connection.execute("PRAGMA journal_mode").fetchone()[0]
    )

    assert journal_mode == "wal"
