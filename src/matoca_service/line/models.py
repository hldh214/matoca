from dataclasses import dataclass
from datetime import datetime


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
