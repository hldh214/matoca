from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from json import JSONDecodeError
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParameter
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from matoca_service.config import RuntimeSettings
from matoca_service.console import MerchantConsoleData
from matoca_service.matoca.models import Shop, Waiting
from matoca_service.service import (
    DashboardData,
    MatocaService,
    MerchantSnapshot,
    MerchantSummary,
    PartyPreferences,
    QueueSubmission,
    QueueUnavailableError,
    UnknownMerchantError,
)
from matoca_service.web.timezone import localize_datetime, parse_timezone

WEB_ROOT = Path(__file__).parent


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    parsed = urlsplit(origin) if origin is not None else None
    forwarded_scheme = request.headers.get("x-forwarded-proto")
    expected_scheme = (
        forwarded_scheme.split(",", maxsplit=1)[0].strip()
        if forwarded_scheme is not None
        else request.url.scheme
    )
    if (
        parsed is None
        or parsed.scheme != expected_scheme
        or parsed.netloc != request.headers.get("host")
    ):
        raise HTTPException(status_code=403, detail="この操作は許可されていません")


class DashboardService(Protocol):
    def list_merchants(self) -> list[MerchantSummary]: ...

    async def party_preferences(self) -> PartyPreferences: ...

    async def update_party_preferences(
        self, party_preferences: PartyPreferences
    ) -> PartyPreferences: ...

    async def merchant_snapshot(
        self,
        merchant_key: str,
        *,
        force_catalog: bool = False,
    ) -> MerchantSnapshot: ...

    async def merchant_console(self, merchant_key: str) -> MerchantConsoleData: ...

    async def dashboard(
        self,
        merchant_key: str,
        keyword: str | None,
        page: int = 1,
    ) -> DashboardData: ...

    async def shop_detail(self, merchant_key: str, shop_id: int) -> Shop: ...

    async def waiting_detail(self, merchant_key: str, waiting_id: int) -> Waiting: ...

    async def current_waiting(self, merchant_key: str) -> list[Waiting]: ...

    async def create_waiting(self, merchant_key: str, submission: QueueSubmission) -> Waiting: ...

    async def cancel_waiting(self, merchant_key: str, waiting_id: int) -> None: ...


class CollectionLifecycle(Protocol):
    def start(self) -> None: ...

    async def stop(self) -> None: ...


def create_app(
    service: DashboardService | None = None,
    *,
    collection_coordinator: CollectionLifecycle | None = None,
) -> FastAPI:
    dashboard_service: DashboardService
    coordinator: CollectionLifecycle | None
    if service is None:
        settings = RuntimeSettings()
        dashboard_service = MatocaService(
            settings.line_client_file,
            settings.state_file,
            settings.database_file,
        )
        coordinator = collection_coordinator or dashboard_service.collection_coordinator
    else:
        dashboard_service = service
        coordinator = collection_coordinator

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        if coordinator is not None:
            coordinator.start()
        try:
            yield
        finally:
            if coordinator is not None:
                await coordinator.stop()

    app = FastAPI(title="Matoca", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=WEB_ROOT / "templates")

    @app.exception_handler(UnknownMerchantError)
    async def unknown_merchant_handler(
        request: Request,
        error: UnknownMerchantError,
    ) -> JSONResponse:
        del request, error
        return JSONResponse(status_code=404, content={"detail": "加盟店が見つかりません"})

    @app.exception_handler(QueueUnavailableError)
    async def queue_unavailable_handler(
        request: Request,
        error: QueueUnavailableError,
    ) -> JSONResponse:
        del request
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.get("/", response_class=HTMLResponse)
    async def merchant_selector_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"merchants": dashboard_service.list_merchants()},
        )

    @app.get("/merchants/{merchant_key}", response_class=HTMLResponse)
    async def merchant_page(request: Request, merchant_key: str) -> HTMLResponse:
        merchants = {item.key: item for item in dashboard_service.list_merchants()}
        if merchant_key not in merchants:
            raise HTTPException(status_code=404, detail="加盟店が見つかりません")
        return templates.TemplateResponse(
            request=request,
            name="merchant.html",
            context={"merchant": merchants[merchant_key]},
        )

    @app.get("/api/dashboard", response_model=DashboardData)
    async def dashboard_api(
        merchant: str = Query(default="sawayaka"),
        keyword: str | None = Query(default=None, max_length=80),
        page: int = Query(default=1, ge=1),
    ) -> DashboardData:
        return await dashboard_service.dashboard(merchant, keyword, page)

    @app.get("/api/preferences", response_model=PartyPreferences)
    async def party_preferences_api() -> PartyPreferences:
        return await dashboard_service.party_preferences()

    @app.put("/api/preferences", response_model=PartyPreferences)
    async def update_party_preferences_api(request: Request) -> PartyPreferences | JSONResponse:
        require_same_origin(request)
        try:
            party_preferences = PartyPreferences.model_validate(await request.json())
        except JSONDecodeError, UnicodeDecodeError, ValidationError:
            return JSONResponse(status_code=422, content={"detail": "入力内容が正しくありません"})
        return await dashboard_service.update_party_preferences(party_preferences)

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

    @app.get("/api/merchants", response_model=list[MerchantSummary])
    async def merchants_api() -> list[MerchantSummary]:
        return dashboard_service.list_merchants()

    @app.get("/api/merchants/{merchant_key}/console", response_model=MerchantConsoleData)
    async def merchant_console_api(merchant_key: str, request: Request) -> MerchantConsoleData:
        console = await dashboard_service.merchant_console(merchant_key)
        timezone = parse_timezone(request.headers.get("x-timezone"))
        return console.model_copy(
            update={
                "updated_at": localize_datetime(console.updated_at, timezone),
                "shops": [
                    shop.model_copy(
                        update={
                            "updated_at": (
                                localize_datetime(shop.updated_at, timezone)
                                if shop.updated_at is not None
                                else None
                            )
                        }
                    )
                    for shop in console.shops
                ],
            }
        )

    @app.post("/api/merchants/{merchant_key}/refresh", response_model=MerchantSnapshot)
    async def refresh_merchant_api(merchant_key: str, request: Request) -> MerchantSnapshot:
        require_same_origin(request)
        snapshot = await dashboard_service.merchant_snapshot(merchant_key, force_catalog=True)
        timezone = parse_timezone(request.headers.get("x-timezone"))
        return snapshot.model_copy(
            update={"refreshed_at": localize_datetime(snapshot.refreshed_at, timezone)}
        )

    @app.post("/api/merchants/{merchant_key}/waiting", response_model=Waiting)
    async def create_waiting_api(
        merchant_key: str,
        submission: QueueSubmission,
        request: Request,
    ) -> Waiting:
        require_same_origin(request)
        return await dashboard_service.create_waiting(merchant_key, submission)

    @app.get("/api/merchants/{merchant_key}/waiting", response_model=list[Waiting])
    async def current_waiting_api(merchant_key: str) -> list[Waiting]:
        return await dashboard_service.current_waiting(merchant_key)

    @app.delete("/api/merchants/{merchant_key}/waiting/{waiting_id}", status_code=204)
    async def cancel_waiting_api(
        request: Request,
        merchant_key: str,
        waiting_id: int = PathParameter(ge=1),
    ) -> None:
        require_same_origin(request)
        await dashboard_service.cancel_waiting(merchant_key, waiting_id)

    return app


def run() -> None:
    settings = RuntimeSettings()
    uvicorn.run(
        create_app(),
        host=settings.host,
        port=settings.port,
        reload=False,
    )
