import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class StateLockBusy(RuntimeError):
    """Raised when a nonblocking state lock cannot be acquired."""


@contextmanager
def exclusive_file_lock(path: Path, *, blocking: bool = True) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    operation = fcntl.LOCK_EX
    if not blocking:
        operation |= fcntl.LOCK_NB

    try:
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as error:
            raise StateLockBusy("authentication state is locked by another writer") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
