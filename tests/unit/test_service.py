import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from matoca_service.collection.models import CollectionCycle
from matoca_service.line.models import LiffToken
from matoca_service.matoca.client import MatocaApiError
from matoca_service.matoca.models import Shop, ShopForms, Waiting
from matoca_service.service import (
    DashboardData,
    MatocaService,
    QueueSubmission,
    QueueUnavailableError,
    validate_queue_submission,
)
from matoca_service.state.models import AppState, LiffTokenState, LineState
from matoca_service.state.store import JsonStateStore

type JwtFactory = Callable[[dict[str, Any]], str]


def test_queue_submission_requires_currently_issuable_shop() -> None:
    shop = Shop(
        id=3272,
        name="synthetic merchant",
        lat=34.0,
        lng=137.0,
        is_open=True,
        is_issuable=False,
    )
    submission = QueueSubmission(shop_id=3272, adult_count=2, child_count=0)

    with pytest.raises(QueueUnavailableError, match="受付状況が変更されました"):
        validate_queue_submission(shop, submission, [])


def test_queue_submission_rejects_second_active_queue() -> None:
    shop = Shop(
        id=3272,
        name="synthetic merchant",
        lat=34.0,
        lng=137.0,
        is_open=True,
        is_issuable=True,
        forms=ShopForms(min_adult=1, max_adult=20, min_child=0, max_child=0),
    )
    submission = QueueSubmission(shop_id=3272, adult_count=2, child_count=0)

    with pytest.raises(QueueUnavailableError, match="すでに受付中"):
        validate_queue_submission(shop, submission, [Waiting(id=125000001)])


class SerializedTestService(MatocaService):
    def __init__(self) -> None:
        self._operation_lock = asyncio.Lock()
        self.active = 0
        self.max_active = 0

    async def _dashboard_unlocked(
        self,
        merchant_key: str,
        keyword: str | None,
        page: int = 1,
    ) -> DashboardData:
        del merchant_key, keyword, page
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return DashboardData(
            merchant="Sawayaka",
            native_access_expires_at=datetime(2026, 9, 17, tzinfo=UTC),
            liff_expires_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
            shops=[],
            waiting=[],
        )


@pytest.mark.asyncio
async def test_dashboard_serializes_token_state_operations() -> None:
    service = SerializedTestService()

    await asyncio.gather(
        service.dashboard("sawayaka", None),
        service.dashboard("sawayaka", None),
    )

    assert service.max_active == 1


class FakeCycleClient:
    def __init__(
        self,
        base_shops: list[Shop],
        details: dict[int, Shop],
        *,
        detail_error: Exception | None = None,
    ) -> None:
        self.base_shops = base_shops
        self.details = details
        self.detail_error = detail_error
        self.events: list[str] = []
        self.active_details = 0
        self.max_active_details = 0

    async def list_all_shops(self) -> list[Shop]:
        self.events.append("list")
        return self.base_shops

    async def get_shop(self, shop_id: int) -> Shop:
        assert self.events[0] == "list"
        self.active_details += 1
        self.max_active_details = max(self.max_active_details, self.active_details)
        try:
            await asyncio.sleep(0.01)
            if self.detail_error is not None:
                raise self.detail_error
            return self.details[shop_id]
        finally:
            self.active_details -= 1

    async def list_waiting(self) -> list[Waiting]:
        self.events.append("waiting")
        return [Waiting(id=42)]


class CycleReadTestService(MatocaService):
    def __init__(self, client: FakeCycleClient) -> None:
        self._operation_lock = asyncio.Lock()
        self.client = client

    async def _authenticated_read(self, merchant_key: str, operation: Any) -> Any:
        assert merchant_key == "sawayaka"
        return await operation(self.client)


@pytest.mark.asyncio
async def test_read_collection_cycle_enriches_list_shops_with_at_most_four_details() -> None:
    base_shops = [
        Shop(
            id=shop_id,
            name=f"List {shop_id}",
            address=f"Address {shop_id}",
            current_waiting=shop_id,
        )
        for shop_id in range(1, 7)
    ]
    details = {
        shop_id: Shop(
            id=shop_id,
            name=f"Detail {shop_id}",
            current_waiting=999,
            is_open=True,
            is_issuable=True,
        )
        for shop_id in range(1, 7)
    }
    service = CycleReadTestService(FakeCycleClient(base_shops, details))

    cycle = await service.read_collection_cycle("sawayaka")

    assert isinstance(cycle, CollectionCycle)
    assert [item.shop.id for item in cycle.shops] == [1, 2, 3, 4, 5, 6]
    assert [item.shop.name for item in cycle.shops] == [
        f"List {shop_id}" for shop_id in range(1, 7)
    ]
    assert [item.shop.current_waiting for item in cycle.shops] == [1, 2, 3, 4, 5, 6]
    assert all(item.detail_fresh for item in cycle.shops)
    assert cycle.waiting == [Waiting(id=42)]
    assert service.client.max_active_details == 4


@pytest.mark.asyncio
async def test_read_collection_cycle_marks_failed_detail_unobserved_and_keeps_list_waiting(
) -> None:
    base_shop = Shop(
        id=1,
        name="List 1",
        current_waiting=10,
        waiting_time={"minutes": 20},
        is_open=True,
        is_issuable=True,
        forms=ShopForms(min_adult=1, max_adult=4),
    )
    service = CycleReadTestService(
        FakeCycleClient([base_shop], {}, detail_error=httpx.ReadTimeout("timeout"))
    )

    cycle = await service.read_collection_cycle("sawayaka")

    item = cycle.shops[0]
    assert item.shop.current_waiting == 10
    assert item.shop.waiting_time is None
    assert item.shop.is_open is False
    assert item.shop.is_issuable is False
    assert item.shop.forms is None
    assert item.detail_fresh is False
    assert item.error_code == "timeout"


def status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/shops/1")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("status", request=request, response=response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("detail_error", "expected_code"),
    [
        (httpx.ConnectError("offline"), "transport"),
        (MatocaApiError("malformed upstream response"), "malformed_response"),
        (status_error(429), "rate_limited"),
        (status_error(500), "http_500"),
    ],
)
async def test_read_collection_cycle_classifies_non_auth_detail_errors(
    detail_error: Exception,
    expected_code: str,
) -> None:
    service = CycleReadTestService(
        FakeCycleClient(
            [Shop(id=1, name="List 1", current_waiting=10)],
            {},
            detail_error=detail_error,
        )
    )

    cycle = await service.read_collection_cycle("sawayaka")

    assert cycle.shops[0].detail_fresh is False
    assert cycle.shops[0].error_code == expected_code


@pytest.mark.asyncio
@respx.mock
async def test_dashboard_reissues_liff_once_after_matoca_unauthorized(
    tmp_path: Path,
    jwt_factory: JwtFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "line_client.toml"
    config_path.write_text(
        """
host = "legy-jp.line-apps.com"
application = "ANDROIDSECONDARY\\t26.11.0\\tAndroid OS\\t14"
locale = "en_US"
protocol_version = "1"
user_agent = "Line/26.11.0"

""".strip(),
        encoding="utf-8",
    )
    access = jwt_factory(
        {
            "jti": "access",
            "rtid": "family",
            "aud": "LINE",
            "iat": 1_789_000_000,
            "exp": 1_894_000_000,
        }
    )
    refresh = jwt_factory(
        {
            "jti": "family",
            "ati": "access",
            "aud": "LINE",
            "aid": "u-synthetic",
            "iat": 1_789_000_000,
            "exp": 1_925_000_000,
        }
    )
    state_path = tmp_path / "state.json"
    JsonStateStore(state_path).save(
        AppState(
            line=LineState(
                access_token=access,
                refresh_token=refresh,
                adid="device-id",
            ),
            liff_tokens={
                "2006055787-m6P6OJ38": LiffTokenState(
                    access_token="stale-liff",
                    issued_at=datetime(2026, 9, 10, tzinfo=UTC),
                    expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                )
            },
        )
    )

    async def issue_fresh_liff(*args: object, **kwargs: object) -> LiffToken:
        del args, kwargs
        return LiffToken(
            access_token="fresh-liff",
            id_token="id-token",
            context_token="context-token",
            issued_at=datetime(2026, 9, 10, tzinfo=UTC),
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )

    monkeypatch.setattr("matoca_service.line.liff.LiffClient.issue_view", issue_fresh_liff)
    auth_route = respx.post("https://admin.junbanmachi.jp/liff/auth").mock(
        side_effect=[
            httpx.Response(401, json={"status": "error"}),
            httpx.Response(200, json={"status": "success", "code": 200}),
        ]
    )
    respx.get("https://admin.junbanmachi.jp/liff/shops").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "code": 200, "content": {"shops": []}},
        )
    )
    respx.get("https://admin.junbanmachi.jp/liff/waiting").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "code": 200, "content": []},
        )
    )

    result = await MatocaService(config_path, state_path).dashboard("sawayaka", None)

    assert result.shops == []
    assert [call.request.headers["authorization"] for call in auth_route.calls] == [
        "Bearer stale-liff",
        "Bearer fresh-liff",
    ]
    assert (
        JsonStateStore(state_path).load().liff_tokens["2006055787-m6P6OJ38"].access_token
        == "fresh-liff"
    )


@pytest.mark.asyncio
@respx.mock
async def test_shop_detail_reissues_liff_once_after_unauthorized(
    tmp_path: Path,
    jwt_factory: JwtFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "line_client.toml"
    config_path.write_text(
        """
host = "legy-jp.line-apps.com"
application = "ANDROIDSECONDARY\\t26.11.0\\tAndroid OS\\t14"
locale = "en_US"
protocol_version = "1"
user_agent = "Line/26.11.0"

""".strip(),
        encoding="utf-8",
    )
    access = jwt_factory(
        {
            "jti": "access",
            "rtid": "family",
            "aud": "LINE",
            "iat": 1_789_000_000,
            "exp": 1_894_000_000,
        }
    )
    refresh = jwt_factory(
        {
            "jti": "family",
            "ati": "access",
            "aud": "LINE",
            "aid": "u-synthetic",
            "iat": 1_789_000_000,
            "exp": 1_925_000_000,
        }
    )
    state_path = tmp_path / "state.json"
    JsonStateStore(state_path).save(
        AppState(
            line=LineState(
                access_token=access,
                refresh_token=refresh,
                adid="device-id",
            ),
            liff_tokens={
                "2006055787-m6P6OJ38": LiffTokenState(
                    access_token="stale-liff",
                    issued_at=datetime(2026, 9, 10, tzinfo=UTC),
                    expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                )
            },
        )
    )

    async def issue_fresh_liff(*args: object, **kwargs: object) -> LiffToken:
        del args, kwargs
        return LiffToken(
            access_token="fresh-liff",
            id_token="synthetic-id-token",
            context_token="synthetic-context-token",
            issued_at=datetime(2026, 9, 10, tzinfo=UTC),
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )

    monkeypatch.setattr("matoca_service.line.liff.LiffClient.issue_view", issue_fresh_liff)
    auth_route = respx.post("https://admin.junbanmachi.jp/liff/auth").mock(
        side_effect=[
            httpx.Response(401, json={"status": "error"}),
            httpx.Response(200, json={"status": "success", "code": 200}),
        ]
    )
    detail_route = respx.get("https://admin.junbanmachi.jp/liff/shops/3272").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "code": 200,
                "content": {"shop": {"id": 3272, "name": "synthetic merchant"}},
            },
        )
    )

    result = await MatocaService(config_path, state_path).shop_detail("sawayaka", 3272)

    assert result.id == 3272
    assert len(auth_route.calls) == 2
    assert len(detail_route.calls) == 1
    assert [call.request.headers["authorization"] for call in auth_route.calls] == [
        "Bearer stale-liff",
        "Bearer fresh-liff",
    ]
    assert (
        JsonStateStore(state_path).load().liff_tokens["2006055787-m6P6OJ38"].access_token
        == "fresh-liff"
    )
