import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from matoca_service.state.locking import exclusive_file_lock
from matoca_service.state.models import AppState


class JsonStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name("state.lock")

    @contextmanager
    def locked(self, *, blocking: bool = True) -> Iterator[None]:
        with exclusive_file_lock(self.lock_path, blocking=blocking):
            yield

    def load(self) -> AppState:
        return AppState.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, state: AppState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(state.model_dump(mode="json"), temporary_file, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
            temporary_path = None
            self._fsync_parent()
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _fsync_parent(self) -> None:
        descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
