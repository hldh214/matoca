import os
import stat
from pathlib import Path

import pytest

from matoca_service.state.locking import StateLockBusy
from matoca_service.state.models import AppState, LineState
from matoca_service.state.store import JsonStateStore


@pytest.fixture
def valid_state() -> AppState:
    return AppState(
        version=1,
        line=LineState(
            access_token="access-secret",
            refresh_token="refresh-secret",
            adid="device-id",
        ),
    )


def test_save_atomically_replaces_state_with_mode_0600(
    tmp_path: Path,
    valid_state: AppState,
) -> None:
    store = JsonStateStore(tmp_path / "state.json")

    store.save(valid_state)

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.load() == valid_state
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_fsyncs_file_and_parent_directory(
    tmp_path: Path,
    valid_state: AppState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JsonStateStore(tmp_path / "state.json")
    real_fsync = os.fsync
    fsynced: list[int] = []

    def recording_fsync(fd: int) -> None:
        fsynced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    store.save(valid_state)

    assert len(fsynced) == 2


def test_nonblocking_second_writer_reports_lock_contention(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path / "state.json")

    with store.locked(), pytest.raises(StateLockBusy), store.locked(blocking=False):
        pass


def test_lock_file_uses_mode_0600(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path / "state.json")

    with store.locked():
        assert stat.S_IMODE(store.lock_path.stat().st_mode) == 0o600
