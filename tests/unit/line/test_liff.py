from datetime import UTC, datetime

import httpx
import pytest
import respx

from matoca_service.config import LineConfig, MerchantConfig
from matoca_service.line.liff import LiffClient
from matoca_service.line.thrift_codec import LiffViewReply


@pytest.fixture
def line_config() -> LineConfig:
    return LineConfig(
        host="legy-jp.line-apps.com",
        application="ANDROIDSECONDARY\t26.11.0\tAndroid OS\t14",
        locale="en_US",
        protocol_version="1",
        user_agent="Line/26.11.0",
    )


@pytest.fixture
def merchant() -> MerchantConfig:
    return MerchantConfig(
        name="Sawayaka",
        liff_id="2006055787-m6P6OJ38",
        api_base_url="https://admin.junbanmachi.jp",
        origin="https://exclusive-mini.junbanmachi.jp",
        entry_url="https://exclusive-mini.junbanmachi.jp/sawayaka/",
        line_entry_url="line://app/2006055787-m6P6OJ38",
    )


@pytest.mark.asyncio
@respx.mock
async def test_issue_view_uses_current_access_and_merchant_liff_id(
    line_config: LineConfig,
    merchant: MerchantConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "matoca_service.line.liff.decode_liff_view_reply",
        lambda _: LiffViewReply(
            access_token="synthetic-liff-token",
            id_token="synthetic-id-token",
            context_token="synthetic-context-token",
            expires_in=43_200,
        ),
    )
    route = respx.post("https://legy-jp.line-apps.com/LIFF1").mock(
        return_value=httpx.Response(200, content=b"synthetic")
    )

    async with httpx.AsyncClient() as http:
        client = LiffClient(line_config, http, now=lambda: datetime(2026, 9, 10, tzinfo=UTC))
        token = await client.issue_view(
            access_token="current-access",
            line_user_id="u-synthetic",
            adid="device-id",
            merchant=merchant,
        )

    request = route.calls[0].request
    assert request.headers["x-line-access"] == "current-access"
    assert request.headers["x-line-liff-id"] == merchant.liff_id
    assert b"u-synthetic" in request.content
    assert b"device-id" in request.content
    assert token.access_token == "synthetic-liff-token"
    assert token.expires_at == datetime(2026, 9, 10, 12, tzinfo=UTC)
