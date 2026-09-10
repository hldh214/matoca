from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from matoca_service.config import MerchantConfig
from matoca_service.line.models import LiffToken, NativeTokenPair
from matoca_service.line.token_manager import TokenManager
from matoca_service.state.models import AppState, LiffTokenState, LineState

type JwtFactory = Callable[[dict[str, Any]], str]


def merchant_config(liff_id: str = "2006055787-m6P6OJ38") -> MerchantConfig:
    return MerchantConfig(
        name="Sawayaka",
        liff_id=liff_id,
        api_base_url="https://admin.junbanmachi.jp",
        origin="https://exclusive-mini.junbanmachi.jp",
        entry_url="https://exclusive-mini.junbanmachi.jp/sawayaka/",
        line_entry_url=f"line://app/{liff_id}",
    )


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


class RecordingLiffClient:
    def __init__(self, token: LiffToken) -> None:
        self.token = token
        self.calls = 0

    async def issue_view(self, **kwargs: object) -> LiffToken:
        del kwargs
        self.calls += 1
        return self.token


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


@pytest.mark.asyncio
async def test_manager_persists_new_liff_token_by_liff_id(jwt_factory: JwtFactory) -> None:
    events: list[str] = []
    pair = pair_tokens(jwt_factory)
    state = AppState(
        line=LineState(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            adid="device-id",
        )
    )
    store = RecordingStore(state, events)
    issued_at = datetime(2026, 9, 10, tzinfo=UTC)
    liff = RecordingLiffClient(
        LiffToken(
            access_token="liff-access",
            id_token="id-token",
            context_token="context-token",
            issued_at=issued_at,
            expires_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
        )
    )
    manager = TokenManager(
        store,
        RecordingRefreshClient(pair, events),
        liff_client=liff,
        now=lambda: issued_at,
    )

    status = await manager.ensure_liff_token(
        liff_id="2006055787-m6P6OJ38",
        merchant=merchant_config(),
    )

    assert status.source == "issued"
    assert liff.calls == 1
    assert store.state.liff_tokens["2006055787-m6P6OJ38"].access_token == "liff-access"


@pytest.mark.asyncio
async def test_manager_reuses_valid_cached_liff_token(jwt_factory: JwtFactory) -> None:
    events: list[str] = []
    pair = pair_tokens(jwt_factory)
    state = AppState(
        line=LineState(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            adid="device-id",
        ),
        liff_tokens={
            "liff-id": LiffTokenState(
                access_token="cached-liff",
                issued_at=datetime(2026, 9, 10, tzinfo=UTC),
                expires_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
            )
        },
    )
    store = RecordingStore(state, events)
    liff = RecordingLiffClient(
        LiffToken(
            access_token="unused",
            id_token="unused",
            context_token="unused",
            issued_at=datetime(2026, 9, 10, tzinfo=UTC),
            expires_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
        )
    )
    manager = TokenManager(
        store,
        RecordingRefreshClient(pair, events),
        liff_client=liff,
        now=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )

    status = await manager.ensure_liff_token(liff_id="liff-id", merchant=merchant_config("liff-id"))

    assert status.source == "cache"
    assert status.access_token == "cached-liff"
    assert liff.calls == 0
