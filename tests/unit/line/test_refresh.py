from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from thrift.protocol.TCompactProtocol import TCompactProtocol
from thrift.Thrift import TMessageType, TType
from thrift.transport.TTransport import TMemoryBuffer

from matoca_service.config import LineConfig
from matoca_service.line.refresh import LineRefreshClient


def build_refresh_reply(access_token: str, refresh_token: str) -> bytes:
    transport = TMemoryBuffer()
    protocol = TCompactProtocol(transport)
    protocol.writeMessageBegin("refresh", TMessageType.REPLY, 1)
    protocol.writeStructBegin("refresh_result")
    protocol.writeFieldBegin("success", TType.STRUCT, 0)
    protocol.writeStructBegin("result")
    protocol.writeFieldBegin("accessToken", TType.STRING, 1)
    protocol.writeString(access_token)
    protocol.writeFieldEnd()
    protocol.writeFieldBegin("refreshToken", TType.STRING, 5)
    protocol.writeString(refresh_token)
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeMessageEnd()
    return transport.getvalue()


@pytest.fixture
def line_config() -> LineConfig:
    return LineConfig(
        host="legy-jp.line-apps.com",
        application="ANDROIDSECONDARY\t26.11.0\tAndroid OS\t14",
        locale="en_US",
        protocol_version="1",
        user_agent="Line/26.11.0",
    )


@pytest.mark.asyncio
@respx.mock
async def test_refresh_uses_old_access_header_and_old_refresh_body(
    line_config: LineConfig,
    jwt_factory: Callable[[dict[str, Any]], str],
) -> None:
    old_access = jwt_factory({"jti": "old-access"})
    old_refresh = jwt_factory({"jti": "old-refresh"})
    new_access = jwt_factory({"jti": "new-access"})
    new_refresh = jwt_factory({"jti": "new-refresh"})
    route = respx.post("https://legy-jp.line-apps.com/EXT/auth/tokenrefresh/v1").mock(
        return_value=httpx.Response(200, content=build_refresh_reply(new_access, new_refresh))
    )

    async with httpx.AsyncClient() as http:
        client = LineRefreshClient(line_config, http)
        pair = await client.refresh(old_access, old_refresh)

    request = route.calls[0].request
    assert request.headers["x-line-access"] == old_access
    assert request.headers["x-line-application"] == line_config.application
    assert request.headers["content-type"] == "application/x-thrift"
    assert old_refresh.encode() in request.content
    assert pair.access_token == new_access
    assert pair.refresh_token == new_refresh


@pytest.mark.asyncio
@respx.mock
async def test_report_uses_supplied_access_token(line_config: LineConfig) -> None:
    route = respx.post("https://legy-jp.line-apps.com/EXT/auth/tokenrefresh/v1").mock(
        return_value=httpx.Response(200, content=b"ok")
    )

    async with httpx.AsyncClient() as http:
        client = LineRefreshClient(line_config, http)
        await client.report_refreshed_access_token("current-access")

    request = route.calls[0].request
    assert request.headers["x-line-access"] == "current-access"
    assert b"reportRefreshedAccessToken" in request.content
