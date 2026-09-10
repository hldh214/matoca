from datetime import UTC, datetime

import httpx
import pytest

from matoca_service.service import DashboardData
from matoca_service.web.app import create_app


class FakeDashboardService:
    async def dashboard(self, merchant_key: str, keyword: str | None) -> DashboardData:
        assert merchant_key == "sawayaka"
        assert keyword == "浜松"
        return DashboardData.model_validate(
            {
                "merchant": "Sawayaka",
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


@pytest.mark.asyncio
async def test_dashboard_page_renders_live_business_data_without_tokens() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/", params={"merchant": "sawayaka", "keyword": "浜松"})

    assert response.status_code == 200
    assert "イオンモール浜松市野店" in response.text
    assert "3 組" in response.text
    assert "現在の受付はありません" in response.text
    assert "access_token" not in response.text
    assert "liff-secret" not in response.text


@pytest.mark.asyncio
async def test_dashboard_api_returns_structured_data() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/dashboard",
            params={"merchant": "sawayaka", "keyword": "浜松"},
        )

    assert response.status_code == 200
    assert response.json()["shops"][0]["id"] == 3278
