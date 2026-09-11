import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute

from matoca_service.console import MerchantConsoleData, ShopConsoleItem
from matoca_service.matoca.models import Shop, Waiting
from matoca_service.service import (
    DashboardData,
    MerchantSnapshot,
    MerchantSummary,
    PartyPreferences,
)
from matoca_service.web.app import create_app


class FakeDashboardService:
    def __init__(self) -> None:
        self.preferences = PartyPreferences(default_adult_count=2, default_child_count=0)

    def list_merchants(self) -> list[MerchantSummary]:
        return [
            MerchantSummary(
                key="sawayaka",
                name="炭焼きレストラン さわやか",
                cover_image_url="https://example.test/sawayaka.jpg",
            ),
            MerchantSummary(
                key="la_ohana_yokohamahonmoku",
                name="ラ・オハナ 横浜本牧",
                cover_image_url="https://example.test/la-ohana.png",
            ),
        ]

    async def merchant_snapshot(
        self,
        merchant_key: str,
        *,
        force_catalog: bool = False,
    ) -> MerchantSnapshot:
        assert merchant_key == "sawayaka"
        del force_catalog
        return MerchantSnapshot(
            merchant=MerchantSummary(key="sawayaka", name="炭焼きレストラン さわやか"),
            refreshed_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
            shops=[],
            waiting=[],
        )

    async def merchant_console(self, merchant_key: str) -> MerchantConsoleData:
        assert merchant_key == "sawayaka"
        return MerchantConsoleData(
            merchant=MerchantSummary(key="sawayaka", name="炭焼きレストラン さわやか"),
            updated_at=datetime(2026, 9, 10, 8, tzinfo=UTC),
            stale=False,
            available_count=1,
            total_count=1,
            shops=[
                ShopConsoleItem(
                    id=3272,
                    name="Synthetic Shop",
                    sub_name=None,
                    address=None,
                    image_url=None,
                    current_waiting=3,
                    official_waiting_minutes=20,
                    official_waiting_is_more=False,
                    status="available",
                    status_label="受付可能",
                    can_join=True,
                    stale=False,
                    updated_at=datetime(2026, 9, 10, 8, tzinfo=UTC),
                    forms=None,
                )
            ],
        )

    async def party_preferences(self) -> PartyPreferences:
        return self.preferences

    async def update_party_preferences(self, preferences: PartyPreferences) -> PartyPreferences:
        self.preferences = preferences
        return preferences

    async def dashboard(
        self,
        merchant_key: str,
        keyword: str | None,
        page: int = 1,
    ) -> DashboardData:
        assert merchant_key == "sawayaka"
        assert keyword == "浜松"
        assert page == 2
        return DashboardData.model_validate(
            {
                "merchant": "Sawayaka",
                "page": page,
                "native_access_expires_at": datetime(2026, 9, 17, tzinfo=UTC),
                "liff_expires_at": datetime(2026, 9, 10, 12, tzinfo=UTC),
                "shops": [
                    {
                        "id": 3278,
                        "name": "炭焼きレストラン さわやか",
                        "sub_name": "イオンモール浜松市野店",
                        "current_waiting": 3,
                        "is_holiday": False,
                        "is_suspended": False,
                    }
                ],
                "waiting": [],
            }
        )

    async def shop_detail(self, merchant_key: str, shop_id: int) -> Shop:
        assert merchant_key == "sawayaka"
        assert shop_id == 3272
        return Shop.model_validate(
            {
                "id": shop_id,
                "name": "synthetic merchant",
                "waiting_time": {"minutes": 90, "is_more": True},
            }
        )

    async def waiting_detail(self, merchant_key: str, waiting_id: int) -> Waiting:
        assert merchant_key == "sawayaka"
        assert waiting_id == 125000001
        return Waiting(id=waiting_id, count=72, number=87)


def test_web_module_import_does_not_require_runtime_files(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import matoca_service.web.app"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_home_page_renders_japanese_merchant_selector_without_tokens() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "利用する加盟店を選ぶ" in response.text
    assert "炭焼きレストラン さわやか" in response.text
    assert "/merchants/sawayaka" in response.text
    assert "ラ・オハナ 横浜本牧" in response.text
    assert "/merchants/la_ohana_yokohamahonmoku" in response.text
    assert 'src="https://example.test/la-ohana.png"' in response.text
    assert "ブランド" not in response.text
    assert "access_token" not in response.text
    assert "liff-secret" not in response.text


@pytest.mark.asyncio
async def test_merchant_page_renders_japanese_shop_console_shell() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/merchants/sawayaka")

    assert response.status_code == 200
    for element_id in (
        "settings-button",
        "refresh-button",
        "current-queue",
        "shop-filter",
        "available-count",
        "total-count",
        "shop-search",
        "shop-list",
        "updated-at",
        "join-dialog",
        "settings-dialog",
        "cancel-dialog",
    ):
        assert f'id="{element_id}"' in response.text
    assert "受付可能" in response.text
    assert "すべて" in response.text
    assert "現在の順番待ち" in response.text
    assert "公式目安" in response.text
    assert "設定" in response.text
    assert "地域別" not in response.text
    assert "自動" not in response.text
    assert "予測" not in response.text
    assert response.text.count('class="dialog-close" type="button"') == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("POST", "/api/merchants/sawayaka/refresh", None),
        (
            "POST",
            "/api/merchants/sawayaka/waiting",
            {"shop_id": 3272, "adult_count": 2, "child_count": 0},
        ),
        ("DELETE", "/api/merchants/sawayaka/waiting/125000001", None),
        ("PUT", "/api/preferences", {"default_adult_count": 3, "default_child_count": 1}),
    ],
)
@pytest.mark.parametrize("origin", [None, "https://example.invalid"])
async def test_state_changing_requests_reject_cross_origin(
    method: str,
    path: str,
    json: dict[str, int] | None,
    origin: str | None,
) -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        headers = {"Origin": origin} if origin is not None else {}
        response = await client.request(method, path, json=json, headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "この操作は許可されていません"}


@pytest.mark.asyncio
async def test_preferences_api_reads_and_updates_party_defaults() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        read_response = await client.get("/api/preferences")
        update_response = await client.put(
            "/api/preferences",
            json={"default_adult_count": 3, "default_child_count": 1},
            headers={"Origin": "http://test"},
        )

    assert read_response.status_code == 200
    assert read_response.json() == {"default_adult_count": 2, "default_child_count": 0}
    assert update_response.status_code == 200
    assert update_response.json() == {"default_adult_count": 3, "default_child_count": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"default_adult_count": 2},
        {"default_adult_count": "two", "default_child_count": 1},
        {"default_adult_count": 2.5, "default_child_count": 1},
        {"default_adult_count": 2, "default_child_count": 1, "unexpected": True},
        {"default_adult_count": 21, "default_child_count": 1},
    ],
)
async def test_preferences_api_returns_japanese_validation_errors(payload: object) -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.put(
            "/api/preferences",
            json=payload,
            headers={"Origin": "http://test"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "入力内容が正しくありません"}


@pytest.mark.asyncio
async def test_preferences_api_returns_japanese_error_for_malformed_json() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.put(
            "/api/preferences",
            content=b'{"default_adult_count": 2',
            headers={"Content-Type": "application/json", "Origin": "http://test"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "入力内容が正しくありません"}


@pytest.mark.asyncio
async def test_dashboard_api_retains_structured_validation_detail() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/dashboard", params={"page": 0})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "page"]
    assert response.json()["detail"][0]["type"] == "greater_than_equal"


@pytest.mark.asyncio
async def test_dashboard_api_returns_structured_data() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/dashboard",
            params={"merchant": "sawayaka", "keyword": "浜松", "page": 2},
        )

    assert response.status_code == 200
    assert response.json()["shops"][0]["id"] == 3278
    assert response.json()["page"] == 2


@pytest.mark.asyncio
async def test_cached_console_api_localizes_observed_timestamps() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/merchants/sawayaka/console",
            headers={"X-Timezone": "America/New_York"},
        )

    assert response.status_code == 200
    assert response.json()["shops"][0]["status_label"] == "受付可能"
    assert response.json()["updated_at"].endswith("-04:00")
    assert response.json()["shops"][0]["updated_at"].endswith("-04:00")


@pytest.mark.asyncio
async def test_shop_detail_api_returns_structured_data() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/shops/3272", params={"merchant": "sawayaka"})

    assert response.status_code == 200
    assert response.json()["id"] == 3272
    assert response.json()["waiting_time"] == {"minutes": 90, "is_more": True}


@pytest.mark.asyncio
async def test_waiting_detail_api_returns_structured_data() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/waiting/125000001",
            params={"merchant": "sawayaka"},
        )

    assert response.status_code == 200
    assert response.json()["id"] == 125000001
    assert response.json()["count"] == 72


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/shops/0", "/api/waiting/-1"])
async def test_detail_apis_reject_non_positive_ids(path: str) -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(path)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_obsolete_snapshot_api_is_removed() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/merchants/sawayaka/snapshot")

    assert response.status_code == 404


def test_preserved_api_routes_keep_response_models() -> None:
    app = create_app(FakeDashboardService())
    api_routes = {
        (route.path, method): route.response_model
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert api_routes[("/api/dashboard", "GET")] == DashboardData
    assert api_routes[("/api/merchants/{merchant_key}/console", "GET")] == MerchantConsoleData
    assert api_routes[("/api/shops/{shop_id}", "GET")] == Shop
    assert api_routes[("/api/waiting/{waiting_id}", "GET")] == Waiting
    assert api_routes[("/api/merchants/{merchant_key}/waiting", "GET")] == list[Waiting]
    assert api_routes[("/api/merchants/{merchant_key}/waiting", "POST")] == Waiting
    assert api_routes[("/api/merchants/{merchant_key}/waiting/{waiting_id}", "DELETE")] is None
    assert api_routes[("/api/preferences", "GET")] == PartyPreferences
    assert api_routes[("/api/preferences", "PUT")] == PartyPreferences
    assert api_routes[("/api/merchants/{merchant_key}/refresh", "POST")] == MerchantSnapshot
