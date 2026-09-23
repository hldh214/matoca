import re

import httpx
import pytest

from matoca_service.web.app import create_app
from tests.unit.web.test_app import FakeDashboardService


def test_asset_version_changes_when_imported_module_changes(tmp_path, monkeypatch) -> None:
    import matoca_service.web.app as web

    assets = tmp_path / "static"
    assets.mkdir()
    module = assets / "queue-time.js"
    module.write_text("export const value = 1;")
    monkeypatch.setattr(web, "WEB_ROOT", tmp_path)
    original = web.asset_version()
    assert web.asset_version() == original
    module.write_text("export const value = 2;")
    assert web.asset_version() != original


@pytest.mark.asyncio
async def test_versioned_assets_include_module_dependencies_and_cache_policy() -> None:
    app = create_app(FakeDashboardService())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        page = await client.get("/merchants/sawayaka")
        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text)
        assert len(assets) == 5
        assert page.headers["cache-control"] == "no-store"
        script = next(url for url in assets if url.endswith("/merchant.js"))
        response = await client.get(script)
        assert response.status_code == 200
        assert "immutable" in response.headers["cache-control"]
        for module in re.findall(r'from "\./([^\"]+)"', response.text):
            dependency = await client.get(script.rsplit("/", 1)[0] + "/" + module)
            assert dependency.status_code == 200
            assert "immutable" in dependency.headers["cache-control"]
        legacy = await client.get("/static/merchant.js")
        assert legacy.headers["cache-control"] == "no-cache"
        assert (await client.get("/api/queues")).headers["cache-control"] == "no-store"
        assert (await client.get("/sw.js")).headers["cache-control"] == "no-cache"
        assert (await client.get("/assets/old/merchant.js")).status_code == 404
