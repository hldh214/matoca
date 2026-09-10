import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from matoca_service.service import DashboardData
from matoca_service.web.app import create_app


class FakeDashboardService:
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
async def test_dashboard_page_renders_live_business_data_without_tokens() -> None:
    app = create_app(FakeDashboardService())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/",
            params={"merchant": "sawayaka", "keyword": "浜松", "page": 2},
        )

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
            params={"merchant": "sawayaka", "keyword": "浜松", "page": 2},
        )

    assert response.status_code == 200
    assert response.json()["shops"][0]["id"] == 3278
    assert response.json()["page"] == 2
