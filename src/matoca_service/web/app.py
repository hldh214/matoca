from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from json import JSONDecodeError
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParameter
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from matoca_service.analytics.models import FavoriteState, FavoriteUpdate, ShopHistory
from matoca_service.automation.decisions import TimingDecision
from matoca_service.automation.models import (
    AutomationEditContext,
    AutomationEditRequest,
    AutomationRequest,
    AutomationTask,
)
from matoca_service.automation.replay import ReplayRequest, ReplayResult
from matoca_service.automation.repository import TaskConflictError
from matoca_service.config import RuntimeSettings
from matoca_service.console import MerchantConsoleData
from matoca_service.matoca.models import Shop, Waiting
from matoca_service.notifications.models import PushSubscription
from matoca_service.notifications.service import NotificationService
from matoca_service.prediction.trends import TrendSummary
from matoca_service.service import (
    DashboardData,
    MatocaService,
    MerchantSnapshot,
    MerchantSummary,
    PartyPreferences,
    QueueOutcomeUnknownError,
    QueueSubmission,
    QueueUnavailableError,
    UnknownMerchantError,
)
from matoca_service.tracking.models import QueueIntentSummary, QueueSession
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

    async def queues(self) -> list[QueueSession | QueueIntentSummary]: ...


class CollectionLifecycle(Protocol):
    def start(self) -> None: ...

    async def stop(self) -> None: ...


class AnalyticsService(Protocol):
    async def shop_trend(self, merchant_key: str, shop_id: int) -> TrendSummary: ...
    async def shop_history(self, merchant_key: str, shop_id: int, day: date) -> ShopHistory: ...
    async def favorites(self) -> dict[str, list[int]]: ...
    async def set_favorite(
        self, merchant_key: str, shop_id: int, enabled: bool
    ) -> FavoriteState: ...


class AutomationService(Protocol):
    async def automation_history(self, task_id: str) -> list[TimingDecision]: ...
    async def automation_replay(self, request: ReplayRequest) -> ReplayResult: ...
    async def automation_tasks(self) -> list[AutomationTask]: ...
    async def create_automation_task(self, request: AutomationRequest) -> AutomationTask: ...
    async def automation_edit_context(self, task_id: str) -> AutomationEditContext: ...
    async def edit_automation_task(
        self, task_id: str, request: AutomationEditRequest
    ) -> AutomationTask: ...
    async def cancel_automation_task(self, task_id: str) -> AutomationTask: ...
    async def resolve_automation_task(self, task_id: str) -> AutomationTask: ...
    async def resolve_manual_intent(self, intent_id: str) -> None: ...


def create_app(
    service: DashboardService | None = None,
    *,
    collection_coordinator: CollectionLifecycle | None = None,
    analytics_service: AnalyticsService | None = None,
    notification_service: NotificationService | None = None,
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
    analytics = analytics_service or cast(AnalyticsService, dashboard_service)
    automation = cast(AutomationService, dashboard_service)
    notifications = notification_service or (
        dashboard_service.notifications if isinstance(dashboard_service, MatocaService) else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        if coordinator is not None:
            coordinator.start()
        tracking = (
            dashboard_service.tracking_coordinator
            if isinstance(dashboard_service, MatocaService)
            else None
        )
        if tracking is not None:
            tracking.start()
        automatic = (
            dashboard_service.automation_coordinator
            if isinstance(dashboard_service, MatocaService)
            else None
        )
        if automatic is not None:
            automatic.start()
        if notifications is not None:
            notifications.dispatcher.start()
        try:
            yield
        finally:
            if notifications is not None:
                await notifications.dispatcher.stop()
            if automatic is not None:
                await automatic.stop()
            if tracking is not None:
                await tracking.stop()
            if coordinator is not None:
                await coordinator.stop()

    app = FastAPI(title="Matoca", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=WEB_ROOT / "templates")

    @app.get("/sw.js")
    async def service_worker() -> FileResponse:
        return FileResponse(
            WEB_ROOT / "static" / "sw.js",
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @app.get("/manifest.webmanifest")
    async def manifest() -> FileResponse:
        return FileResponse(
            WEB_ROOT / "static" / "manifest.webmanifest", media_type="application/manifest+json"
        )

    def push_service() -> NotificationService:
        if notifications is None:
            raise HTTPException(503, "通知機能を利用できません")
        return notifications

    @app.get("/api/push/public-key")
    async def push_public_key() -> dict[str, str | None]:
        return {"public_key": await push_service().public_key()}

    @app.post("/api/push/public-key")
    async def push_enable_key(request: Request) -> dict[str, str | None]:
        require_same_origin(request)
        try:
            return {
                "public_key": await push_service().public_key(subject=request.headers["origin"])
            }
        except ValueError:
            raise HTTPException(422, "通知を有効にするにはHTTPSで開いてください") from None

    @app.post("/api/push/subscriptions", status_code=201)
    async def push_subscribe(request: Request) -> dict[str, str]:
        require_same_origin(request)
        try:
            subscription = PushSubscription.model_validate(await request.json())
        except ValidationError, JSONDecodeError, UnicodeDecodeError:
            raise HTTPException(422, "通知先の情報が不正です") from None
        try:
            return {"id": await push_service().subscribe(subscription)}
        except LookupError:
            raise HTTPException(409, "先に通知を有効にしてください") from None

    async def push_field(request: Request, name: str) -> str:
        try:
            payload = await request.json()
        except JSONDecodeError, UnicodeDecodeError:
            raise HTTPException(422, "通知先の情報が不正です") from None
        if (
            not isinstance(payload, dict)
            or set(payload) != {name}
            or not isinstance(payload[name], str)
            or not 0 < len(payload[name]) <= 4096
        ):
            raise HTTPException(422, "通知先の情報が不正です")
        return cast(str, payload[name])

    @app.delete("/api/push/subscriptions", status_code=204)
    async def push_unsubscribe(request: Request) -> Response:
        require_same_origin(request)
        await push_service().unsubscribe(await push_field(request, "endpoint"))
        return Response(status_code=204)

    @app.post("/api/push/test", status_code=202)
    async def push_test(request: Request) -> dict[str, str]:
        require_same_origin(request)
        identity = await push_field(request, "subscription_id")
        try:
            await push_service().test(identity)
        except LookupError:
            raise HTTPException(404, "このブラウザーの通知を有効にしてください") from None
        return {"detail": "テスト通知を送信待ちに追加しました"}

    @app.get("/api/push/history")
    async def push_history() -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in await push_service().history()]

    @app.exception_handler(TaskConflictError)
    async def task_conflict_handler(request: Request, error: TaskConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.get("/api/automation/tasks", response_model=list[AutomationTask])
    async def automation_tasks_api() -> list[AutomationTask]:
        return await automation.automation_tasks()

    @app.get("/api/automation/tasks/{task_id}/history", response_model=list[TimingDecision])
    async def automation_history_api(task_id: str) -> list[TimingDecision]:
        try:
            return await automation.automation_history(task_id)
        except LookupError as error:
            raise HTTPException(404, "自動受付が見つかりません") from error

    @app.get("/api/automation/replay", response_model=ReplayResult)
    async def automation_replay_api(request: Request) -> ReplayResult | JSONResponse:
        try:
            payload = ReplayRequest.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "加盟店・店舗・日本時間の日付・時差を含む到着予定を確認してください"
                },
            )
        return await automation.automation_replay(payload)

    @app.post("/api/automation/tasks", response_model=AutomationTask, status_code=201)
    async def create_automation_api(request: Request) -> AutomationTask | JSONResponse:
        require_same_origin(request)
        try:
            payload = AutomationRequest.model_validate(await request.json())
        except JSONDecodeError, UnicodeDecodeError, ValidationError:
            return JSONResponse(
                status_code=422, content={"detail": "到着予定・人数・同意内容を確認してください"}
            )
        return await automation.create_automation_task(payload)

    @app.get("/api/automation/tasks/{task_id}/edit", response_model=AutomationEditContext)
    async def automation_edit_context_api(task_id: str) -> AutomationEditContext:
        try:
            return await automation.automation_edit_context(task_id)
        except LookupError as error:
            raise HTTPException(404, "自動受付が見つかりません") from error

    @app.put("/api/automation/tasks/{task_id}", response_model=AutomationTask)
    async def edit_automation_api(task_id: str, request: Request) -> AutomationTask | JSONResponse:
        require_same_origin(request)
        try:
            payload = AutomationEditRequest.model_validate(await request.json())
        except JSONDecodeError, UnicodeDecodeError, ValidationError:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "到着予定・人数・同意内容を確認してください。"
                    "加盟店・店舗・実行方法は変更できません"
                },
            )
        try:
            return await automation.edit_automation_task(task_id, payload)
        except LookupError as error:
            raise HTTPException(404, "自動受付が見つかりません") from error

    @app.delete("/api/automation/tasks/{task_id}", response_model=AutomationTask)
    async def cancel_automation_api(task_id: str, request: Request) -> AutomationTask:
        require_same_origin(request)
        try:
            return await automation.cancel_automation_task(task_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="自動受付が見つかりません") from error

    @app.post("/api/automation/tasks/{task_id}/resolve", response_model=AutomationTask)
    async def resolve_automation_api(task_id: str, request: Request) -> AutomationTask:
        require_same_origin(request)
        await require_absence_confirmation(request)
        try:
            return await automation.resolve_automation_task(task_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="自動受付が見つかりません") from error

    async def require_absence_confirmation(request: Request) -> None:
        try:
            payload = await request.json()
        except JSONDecodeError, UnicodeDecodeError:
            raise HTTPException(status_code=422, detail="確認内容が正しくありません") from None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"confirm_no_queue"}
            or payload["confirm_no_queue"] is not True
        ):
            raise HTTPException(status_code=422, detail="受付がないことを確認してください")

    @app.post("/api/queues/intents/{intent_id}/resolve", status_code=204)
    async def resolve_manual_intent_api(intent_id: str, request: Request) -> None:
        require_same_origin(request)
        await require_absence_confirmation(request)
        await automation.resolve_manual_intent(intent_id)

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

    @app.exception_handler(QueueOutcomeUnknownError)
    async def queue_unknown_handler(
        request: Request, error: QueueOutcomeUnknownError
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=503, content={"detail": str(error), "code": "queue_outcome_unknown"}
        )

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

    @app.get("/api/queues", response_model=list[QueueSession | QueueIntentSummary])
    async def queues_api() -> list[QueueSession | QueueIntentSummary]:
        return await dashboard_service.queues()

    @app.get("/api/favorites", response_model=dict[str, list[int]])
    async def favorites_api() -> dict[str, list[int]]:
        return await analytics.favorites()

    @app.get("/api/merchants/{merchant_key}/shops/{shop_id}/history", response_model=ShopHistory)
    async def shop_history_api(
        merchant_key: str, day: date, shop_id: int = PathParameter(ge=1)
    ) -> ShopHistory:
        try:
            return await analytics.shop_history(merchant_key, shop_id, day)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="店舗が見つかりません") from error

    @app.get("/api/merchants/{merchant_key}/shops/{shop_id}/trend", response_model=TrendSummary)
    async def shop_trend_api(merchant_key: str, shop_id: int = PathParameter(ge=1)) -> TrendSummary:
        try:
            return await analytics.shop_trend(merchant_key, shop_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="店舗が見つかりません") from error

    @app.put("/api/merchants/{merchant_key}/shops/{shop_id}/favorite", response_model=FavoriteState)
    async def favorite_api(
        request: Request, merchant_key: str, shop_id: int = PathParameter(ge=1)
    ) -> FavoriteState | JSONResponse:
        require_same_origin(request)
        try:
            update = FavoriteUpdate.model_validate(await request.json())
        except JSONDecodeError, UnicodeDecodeError, ValidationError:
            return JSONResponse(status_code=422, content={"detail": "入力内容が正しくありません"})
        try:
            return await analytics.set_favorite(merchant_key, shop_id, update.enabled)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="店舗が見つかりません") from error

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
