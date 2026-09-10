from pathlib import Path
from typing import Protocol

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from matoca_service.config import RuntimeSettings
from matoca_service.service import DashboardData, MatocaService

WEB_ROOT = Path(__file__).parent


class DashboardService(Protocol):
    async def dashboard(self, merchant_key: str, keyword: str | None) -> DashboardData: ...


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
    ) -> HTMLResponse:
        data = await dashboard_service.dashboard(merchant, keyword)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"data": data, "merchant_key": merchant, "keyword": keyword or ""},
        )

    @app.get("/api/dashboard", response_model=DashboardData)
    async def dashboard_api(
        merchant: str = Query(default="sawayaka"),
        keyword: str | None = Query(default=None, max_length=80),
    ) -> DashboardData:
        return await dashboard_service.dashboard(merchant, keyword)

    return app


app = create_app()


def run() -> None:
    settings = RuntimeSettings()
    uvicorn.run(
        "matoca_service.web.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
