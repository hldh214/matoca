from pathlib import Path
from typing import cast

import httpx
import pytest

from matoca_service.notifications.keys import VapidKeys
from matoca_service.notifications.repository import NotificationRepository
from matoca_service.state.models import AppState, LineState
from matoca_service.state.store import JsonStateStore
from matoca_service.web.app import DashboardService, create_app
from tests.unit.test_notifications import NOW, database, subscription


@pytest.mark.asyncio
async def test_push_api_requires_gesture_write_origin_and_targets_one_browser(
    tmp_path: Path,
) -> None:
    from matoca_service.notifications.service import NotificationService

    store = JsonStateStore(tmp_path / "state.json")
    store.save(AppState(line=LineState(access_token="access", refresh_token="refresh", adid="d")))
    repo = NotificationRepository(database(tmp_path))
    service = NotificationService(repo, VapidKeys(store))
    app = create_app(cast(DashboardService, object()), notification_service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        assert (await client.get("/api/push/public-key")).json() == {"public_key": None}
        assert (await client.post("/api/push/public-key")).status_code == 403
        headers = {"origin": "https://test"}
        key = await client.post("/api/push/public-key", headers=headers)
        assert len(key.json()["public_key"]) == 87
        one = await client.post(
            "/api/push/subscriptions", headers=headers, json=subscription().model_dump()
        )
        await client.post(
            "/api/push/subscriptions", headers=headers, json=subscription("two").model_dump()
        )
        assert one.status_code == 201
        assert "endpoint" not in one.text
        test = await client.post(
            "/api/push/test", headers=headers, json={"subscription_id": one.json()["id"]}
        )
        assert test.status_code == 202
        assert len(repo.pending(NOW.replace(year=2099))) == 1
        history = await client.get("/api/push/history")
        assert "通知のテスト" in history.text
        assert "fcm.googleapis.com" not in history.text
        malformed = await client.post(
            "/api/push/subscriptions", headers=headers, json={"endpoint": "SECRET", "keys": {}}
        )
        assert malformed.status_code == 422
        assert "SECRET" not in malformed.text
        deleted = await client.request(
            "DELETE",
            "/api/push/subscriptions",
            headers=headers,
            json={"endpoint": subscription().endpoint},
        )
        assert deleted.status_code == 204
        assert repo.pending(NOW.replace(year=2099)) == []


@pytest.mark.asyncio
async def test_pwa_root_scope_and_same_origin_assets() -> None:
    app = create_app(cast(DashboardService, object()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        sw = await c.get("/sw.js")
        assert sw.status_code == 200
        assert sw.headers["service-worker-allowed"] == "/"
        manifest = (await c.get("/manifest.webmanifest")).json()
        assert manifest["start_url"] == "/"
        for icon in manifest["icons"]:
            assert (await c.get(icon["src"])).status_code == 200
