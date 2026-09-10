from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from matoca_service.config import MerchantConfig
from matoca_service.line.jwt import validate_native_pair
from matoca_service.line.models import LiffToken, NativeTokenPair
from matoca_service.state.models import AppState, LiffTokenState


class StateStore(Protocol):
    def locked(self, *, blocking: bool = True) -> AbstractContextManager[None]: ...

    def load(self) -> AppState: ...

    def save(self, state: AppState) -> None: ...


class RefreshClient(Protocol):
    async def report_refreshed_access_token(self, access_token: str) -> None: ...

    async def refresh(self, access_token: str, refresh_token: str) -> NativeTokenPair: ...


class LiffIssuer(Protocol):
    async def issue_view(
        self,
        *,
        access_token: str,
        line_user_id: str,
        adid: str,
        merchant: MerchantConfig,
    ) -> LiffToken: ...


@dataclass(frozen=True, slots=True)
class NativeTokenStatus:
    access_expires_at: datetime
    refresh_expires_at: datetime
    refreshed: bool


@dataclass(frozen=True, slots=True)
class LiffTokenStatus:
    access_token: str = field(repr=False)
    expires_at: datetime
    source: str


class TokenManager:
    REFRESH_THRESHOLD = timedelta(hours=24)
    LIFF_EXPIRY_MARGIN = timedelta(minutes=5)

    def __init__(
        self,
        store: StateStore,
        refresh_client: RefreshClient,
        *,
        liff_client: LiffIssuer | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._refresh_client = refresh_client
        self._liff_client = liff_client
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
                "pending_access_report": True,
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
            if state.line.pending_access_report:
                await self._refresh_client.report_refreshed_access_token(state.line.access_token)
                state = state.model_copy(
                    update={"line": state.line.model_copy(update={"pending_access_report": False})}
                )
                self._store.save(state)
            current = self._current_status(state, refreshed=False)
            if not force and current.access_expires_at - self._now() > self.REFRESH_THRESHOLD:
                return current

            old_access = state.line.access_token
            await self._refresh_client.report_refreshed_access_token(old_access)
            pair = await self._refresh_client.refresh(old_access, state.line.refresh_token)
            new_state = self._normalize(state, pair)
            self._store.save(new_state)
            await self._refresh_client.report_refreshed_access_token(pair.access_token)
            reported_state = new_state.model_copy(
                update={"line": new_state.line.model_copy(update={"pending_access_report": False})}
            )
            self._store.save(reported_state)
            return self._current_status(reported_state, refreshed=True)

    async def ensure_liff_token(
        self,
        *,
        liff_id: str,
        merchant: MerchantConfig,
        force: bool = False,
    ) -> LiffTokenStatus:
        if self._liff_client is None:
            raise RuntimeError("LIFF client is not configured")

        with self._store.locked():
            state = self._store.load()
            cached = state.liff_tokens.get(liff_id)
            if (
                not force
                and cached is not None
                and cached.expires_at - self._now() > self.LIFF_EXPIRY_MARGIN
            ):
                return LiffTokenStatus(
                    access_token=cached.access_token,
                    expires_at=cached.expires_at,
                    source="cache",
                )

            claims = validate_native_pair(state.line.access_token, state.line.refresh_token)
            if claims.aid is None:
                message = "native refresh token does not contain a LINE account identifier"
                raise RuntimeError(message)
            token = await self._liff_client.issue_view(
                access_token=state.line.access_token,
                line_user_id=claims.aid,
                adid=state.line.adid,
                merchant=merchant,
            )
            liff_tokens = dict(state.liff_tokens)
            liff_tokens[liff_id] = LiffTokenState(
                access_token=token.access_token,
                issued_at=token.issued_at,
                expires_at=token.expires_at,
            )
            self._store.save(state.model_copy(update={"liff_tokens": liff_tokens}))
            return LiffTokenStatus(
                access_token=token.access_token,
                expires_at=token.expires_at,
                source="issued",
            )
