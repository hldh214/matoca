import httpx

from matoca_service.config import LineConfig
from matoca_service.line.models import NativeTokenPair
from matoca_service.line.thrift_codec import (
    decode_refresh_reply,
    encode_refresh_call,
    encode_report_refreshed_access_token_call,
)


class LineRefreshClient:
    PATH = "/EXT/auth/tokenrefresh/v1"

    def __init__(self, config: LineConfig, http: httpx.AsyncClient) -> None:
        self._config = config
        self._http = http

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "content-type": "application/x-thrift",
            "user-agent": self._config.user_agent,
            "x-line-access": access_token,
            "x-line-application": self._config.application,
            "x-lal": self._config.locale,
            "x-lpv": self._config.protocol_version,
        }

    async def _post(self, access_token: str, content: bytes) -> bytes:
        response = await self._http.post(
            f"https://{self._config.host}{self.PATH}",
            headers=self._headers(access_token),
            content=content,
        )
        response.raise_for_status()
        return response.content

    async def refresh(self, access_token: str, refresh_token: str) -> NativeTokenPair:
        content = await self._post(access_token, encode_refresh_call(refresh_token))
        reply = decode_refresh_reply(content)
        return NativeTokenPair(
            access_token=reply.access_token,
            refresh_token=reply.refresh_token,
        )

    async def report_refreshed_access_token(self, access_token: str) -> None:
        await self._post(
            access_token,
            encode_report_refreshed_access_token_call(access_token),
        )
