from datetime import UTC, date, datetime

import httpx
import pytest

from matoca_service.analytics.models import FavoriteState, ShopHistory, ShopIdentity
from matoca_service.prediction.trends import TrendSummary, TrendTimeline
from matoca_service.web.app import create_app


class AnalyticsFake:
    async def shop_trend(self, merchant_key: str, shop_id: int) -> TrendSummary:
        if shop_id == 9999:
            raise LookupError(shop_id)
        return TrendTimeline([]).summary(shop_id, datetime(2026, 9, 14, tzinfo=UTC))

    async def shop_history(self, merchant_key: str, shop_id: int, day: date) -> ShopHistory:
        return ShopHistory(
            day=day, shop=ShopIdentity(id=shop_id, name=merchant_key), observations=[]
        )

    async def favorites(self) -> dict[str, list[int]]:
        return {"sawayaka": [3272]}

    async def set_favorite(self, merchant_key: str, shop_id: int, enabled: bool) -> FavoriteState:
        if shop_id == 9999:
            raise LookupError(shop_id)
        return FavoriteState(merchant_key=merchant_key, shop_id=shop_id, enabled=enabled)


@pytest.mark.asyncio
async def test_analytics_routes_return_structured_history_and_favorites() -> None:
    app = create_app(object(), analytics_service=AnalyticsFake())  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        history = await client.get("/api/merchants/sawayaka/shops/3272/history?day=2026-09-10")
        favorites = await client.get("/api/favorites")
    assert history.json() == {
        "day": "2026-09-10",
        "shop": {
            "id": 3272,
            "name": "sawayaka",
            "sub_name": None,
            "address": None,
            "tel": None,
            "lat": None,
            "lng": None,
        },
        "observations": [],
        "trend": None,
    }
    assert favorites.json() == {"sawayaka": [3272]}


@pytest.mark.asyncio
async def test_trend_route_returns_insufficient_evidence_and_unknown_shop() -> None:
    app = create_app(object(), analytics_service=AnalyticsFake())  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/merchants/sawayaka/shops/3272/trend")
        missing = await client.get("/api/merchants/sawayaka/shops/9999/trend")
    assert response.status_code == 200
    assert response.json()["suggested_addition_minutes"] is None
    assert response.json()["sample_count"] == 0
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_favorite_mutation_requires_same_origin_and_boolean() -> None:
    app = create_app(object(), analytics_service=AnalyticsFake())  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        forbidden = await client.put(
            "/api/merchants/sawayaka/shops/3272/favorite", json={"enabled": True}
        )
        invalid = await client.put(
            "/api/merchants/sawayaka/shops/3272/favorite",
            json={"enabled": "yes"},
            headers={"Origin": "http://test"},
        )
        success = await client.put(
            "/api/merchants/sawayaka/shops/3272/favorite",
            json={"enabled": True},
            headers={"Origin": "http://test"},
        )
    assert forbidden.status_code == 403
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "入力内容が正しくありません"}
    assert success.json() == {"merchant_key": "sawayaka", "shop_id": 3272, "enabled": True}


@pytest.mark.asyncio
async def test_favorite_mutation_returns_safe_japanese_not_found() -> None:
    app = create_app(object(), analytics_service=AnalyticsFake())  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/merchants/sawayaka/shops/9999/favorite",
            json={"enabled": True},
            headers={"Origin": "http://test"},
        )
    assert response.status_code == 404
    assert response.json() == {"detail": "店舗が見つかりません"}
