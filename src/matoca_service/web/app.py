from pathlib import Path
from typing import Protocol

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi import Path as PathParameter
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from matoca_service.config import RuntimeSettings
from matoca_service.matoca.models import Shop, Waiting
from matoca_service.service import DashboardData, MatocaService

WEB_ROOT = Path(__file__).parent


class DashboardService(Protocol):
    async def dashboard(
        self,
        merchant_key: str,
        keyword: str | None,
        page: int = 1,
    ) -> DashboardData: ...

    async def shop_detail(self, merchant_key: str, shop_id: int) -> Shop: ...

    async def waiting_detail(self, merchant_key: str, waiting_id: int) -> Waiting: ...


def create_app(service: DashboardService | None = None) -> FastAPI:
    settings = RuntimeSettings()
    dashboard_service = service or MatocaService(settings.config_file, settings.state_file)
    app = FastAPI(title="Matoca", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=WEB_ROOT / "templates")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard_page(
        request: Request,
        merchant: str = Query(default="sawayaka"),
        keyword: str | None = Query(default=None, max_length=80),
        page: int = Query(default=1, ge=1),
    ) -> HTMLResponse:
        data = await dashboard_service.dashboard(merchant, keyword, page)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"data": data, "merchant_key": merchant, "keyword": keyword or ""},
        )

    @app.get("/api/dashboard", response_model=DashboardData)
    async def dashboard_api(
        merchant: str = Query(default="sawayaka"),
        keyword: str | None = Query(default=None, max_length=80),
        page: int = Query(default=1, ge=1),
    ) -> DashboardData:
        return await dashboard_service.dashboard(merchant, keyword, page)

    @app.get("/api/shops/{shop_id}", response_model=Shop)
    async def shop_detail_api(
        shop_id: int = PathParameter(ge=1),
        merchant: str = Query(default="sawayaka"),
    ) -> Shop:
        return await dashboard_service.shop_detail(merchant, shop_id)

    @app.get("/api/waiting/{waiting_id}", response_model=Waiting)
    async def waiting_detail_api(
        waiting_id: int = PathParameter(ge=1),
        merchant: str = Query(default="sawayaka"),
    ) -> Waiting:
        return await dashboard_service.waiting_detail(merchant, waiting_id)

    return app


def run() -> None:
    settings = RuntimeSettings()
    uvicorn.run(
        create_app(),
        host=settings.host,
        port=settings.port,
        reload=False,
    )
