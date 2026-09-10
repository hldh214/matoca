from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from matoca_service.config import LineConfig, MerchantConfig
from matoca_service.line.models import LiffToken
from matoca_service.line.thrift_codec import (
    LiffViewRequest,
    decode_liff_view_reply,
    encode_issue_liff_view_call,
)


class LiffClient:
    PATH = "/LIFF1"

    def __init__(
        self,
        config: LineConfig,
        http: httpx.AsyncClient,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._http = http
        self._now = now or (lambda: datetime.now(tz=UTC))

    def _headers(self, access_token: str, liff_id: str) -> dict[str, str]:
        return {
            "content-type": "application/x-thrift",
            "user-agent": self._config.user_agent,
            "x-line-access": access_token,
            "x-line-application": self._config.application,
            "x-line-liff-id": liff_id,
            "x-lal": self._config.locale,
            "x-lpv": self._config.protocol_version,
        }

    async def issue_view(
        self,
        *,
        access_token: str,
        line_user_id: str,
        adid: str,
        merchant: MerchantConfig,
    ) -> LiffToken:
        separator = "&" if "?" in merchant.line_entry_url else "?"
        line_entry_url = f"{merchant.line_entry_url}{separator}liff.state=%2Fwaiting%2F"
        body = encode_issue_liff_view_call(
            LiffViewRequest(
                liff_id=merchant.liff_id,
                line_user_id=line_user_id,
                adid=adid,
                line_entry_url=line_entry_url,
            )
        )
        response = await self._http.post(
            f"https://{self._config.host}{self.PATH}",
            headers=self._headers(access_token, merchant.liff_id),
            content=body,
        )
        response.raise_for_status()
        reply = decode_liff_view_reply(response.content)
        issued_at = self._now()
        return LiffToken(
            access_token=reply.access_token,
            id_token=reply.id_token,
            context_token=reply.context_token,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=reply.expires_in),
        )
