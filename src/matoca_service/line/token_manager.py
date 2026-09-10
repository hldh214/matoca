from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from matoca_service.line.jwt import validate_native_pair
from matoca_service.line.models import NativeTokenPair
from matoca_service.state.models import AppState


class StateStore(Protocol):
    def locked(self, *, blocking: bool = True) -> AbstractContextManager[None]: ...

    def load(self) -> AppState: ...

    def save(self, state: AppState) -> None: ...


class RefreshClient(Protocol):
    async def report_refreshed_access_token(self, access_token: str) -> None: ...

    async def refresh(self, access_token: str, refresh_token: str) -> NativeTokenPair: ...


@dataclass(frozen=True, slots=True)
class NativeTokenStatus:
    access_expires_at: datetime
    refresh_expires_at: datetime
    refreshed: bool


class TokenManager:
    REFRESH_THRESHOLD = timedelta(hours=24)

    def __init__(
        self,
        store: StateStore,
        refresh_client: RefreshClient,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._refresh_client = refresh_client
        self._now = now or (lambda: datetime.now(tz=UTC))

    def _normalize(self, state: AppState, pair: NativeTokenPair) -> AppState:
        claims = validate_native_pair(pair.access_token, pair.refresh_token)
        line = state.line.model_copy(
            update={
                "access_token": pair.access_token,
                "refresh_token": pair.refresh_token,
                "access_expires_at": claims.access_expires_at,
                "refresh_expires_at": claims.refresh_expires_at,
                "rtid": claims.rtid,
                "aid": claims.aid,
                "lsid": claims.lsid,
                "updated_at": self._now(),
            }
        )
        return state.model_copy(update={"line": line})

    def _current_status(self, state: AppState, *, refreshed: bool) -> NativeTokenStatus:
        claims = validate_native_pair(state.line.access_token, state.line.refresh_token)
        return NativeTokenStatus(
            access_expires_at=claims.access_expires_at,
            refresh_expires_at=claims.refresh_expires_at,
            refreshed=refreshed,
        )

    async def ensure_native_token(self, *, force: bool = False) -> NativeTokenStatus:
        with self._store.locked():
            state = self._store.load()
            current = self._current_status(state, refreshed=False)
            if not force and current.access_expires_at - self._now() > self.REFRESH_THRESHOLD:
                return current

            old_access = state.line.access_token
            await self._refresh_client.report_refreshed_access_token(old_access)
            pair = await self._refresh_client.refresh(old_access, state.line.refresh_token)
            new_state = self._normalize(state, pair)
            self._store.save(new_state)
            await self._refresh_client.report_refreshed_access_token(pair.access_token)
            return self._current_status(new_state, refreshed=True)
