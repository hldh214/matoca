import os
import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from matoca_service.storage.migrations import migrate

T = TypeVar("T")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._wal_initialized = False

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        with self._connect() as connection:
            try:
                migrate(connection)
            finally:
                self._enforce_file_modes()

    def read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with self._connect() as connection:
            return operation(connection)

    def write(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with self._connect() as connection, connection:
            result = operation(connection)
        self._enforce_file_modes()
        return result

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            if not self._wal_initialized:
                connection.execute("PRAGMA journal_mode=WAL")
                self._wal_initialized = True
            yield connection
        finally:
            connection.close()

    def _enforce_file_modes(self) -> None:
        for path in (
            self.path,
            self.path.with_name(f"{self.path.name}-wal"),
            self.path.with_name(f"{self.path.name}-shm"),
        ):
            if path.exists():
                os.chmod(path, 0o600)
