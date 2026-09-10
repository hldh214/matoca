from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class NativeTokenPair:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class LiffToken:
    access_token: str = field(repr=False)
    id_token: str = field(repr=False)
    context_token: str = field(repr=False)
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class NativePairClaims:
    access_jti: str
    rtid: str
    aid: str | None
    lsid: str | None
    audience: str
    scope: str | None
    access_issued_at: datetime
    access_expires_at: datetime
    refresh_issued_at: datetime
    refresh_expires_at: datetime
