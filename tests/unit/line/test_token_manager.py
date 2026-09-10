from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from matoca_service.line.models import NativeTokenPair
from matoca_service.line.token_manager import TokenManager
from matoca_service.state.models import AppState, LineState

type JwtFactory = Callable[[dict[str, Any]], str]


class RecordingStore:
    def __init__(self, state: AppState, events: list[str]) -> None:
        self.state = state
        self.events = events

    @contextmanager
    def locked(self, *, blocking: bool = True) -> Iterator[None]:
        del blocking
        self.events.append("lock")
        yield

    def load(self) -> AppState:
        self.events.append("load")
        return self.state

    def save(self, state: AppState) -> None:
        self.events.append("save:new-pair")
        self.state = state


class RecordingRefreshClient:
    def __init__(
        self,
        pair: NativeTokenPair,
        events: list[str],
        *,
        old_pair: NativeTokenPair | None = None,
    ) -> None:
        self.pair = pair
        self.events = events
        self.old_pair = old_pair or pair

    async def report_refreshed_access_token(self, access_token: str) -> None:
        label = "old" if access_token == self.old_pair.access_token else "new"
        self.events.append(f"report:{label}")

    async def refresh(self, access_token: str, refresh_token: str) -> NativeTokenPair:
        assert access_token == self.old_pair.access_token
        assert refresh_token == self.old_pair.refresh_token
        self.events.append("refresh")
        return self.pair


def pair_tokens(jwt_factory: JwtFactory, *, prefix: str = "new") -> NativeTokenPair:
    access = jwt_factory(
        {
            "jti": f"{prefix}-access",
            "rtid": "family",
            "aud": "LINE",
            "scp": "LINE_CORE",
            "iat": 1_789_000_000,
            "exp": 1_789_604_800,
        }
    )
    refresh = jwt_factory(
        {
            "jti": "family",
            "ati": f"{prefix}-access",
            "aud": "LINE",
            "aid": "u-synthetic",
            "lsid": "session",
            "iat": 1_789_000_000,
            "exp": 1_820_536_000,
        }
    )
    return NativeTokenPair(access_token=access, refresh_token=refresh)


@pytest.mark.asyncio
async def test_manager_persists_pair_before_reporting_new_access(jwt_factory: JwtFactory) -> None:
    events: list[str] = []
    old_pair = pair_tokens(jwt_factory, prefix="old")
    state = AppState(
        line=LineState(
            access_token=old_pair.access_token,
            refresh_token=old_pair.refresh_token,
            adid="device-id",
        )
    )
    store = RecordingStore(state, events)
    client = RecordingRefreshClient(pair_tokens(jwt_factory), events, old_pair=old_pair)
    manager = TokenManager(store, client, now=lambda: datetime(2026, 9, 10, tzinfo=UTC))

    status = await manager.ensure_native_token(force=True)

    assert events == ["lock", "load", "report:old", "refresh", "save:new-pair", "report:new"]
    assert status.refreshed
    assert store.state.line.rtid == "family"
    assert store.state.line.aid == "u-synthetic"


@pytest.mark.asyncio
async def test_manager_does_not_refresh_valid_token_without_force(jwt_factory: JwtFactory) -> None:
    events: list[str] = []
    pair = pair_tokens(jwt_factory)
    state = AppState(
        line=LineState(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            access_expires_at=datetime(2026, 9, 17, tzinfo=UTC),
            refresh_expires_at=datetime(2027, 9, 10, tzinfo=UTC),
            adid="device-id",
        )
    )
    store = RecordingStore(state, events)
    client = RecordingRefreshClient(pair, events)
    manager = TokenManager(store, client, now=lambda: datetime(2026, 9, 10, tzinfo=UTC))

    status = await manager.ensure_native_token()

    assert events == ["lock", "load"]
    assert not status.refreshed
