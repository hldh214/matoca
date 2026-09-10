import asyncio
from datetime import datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from matoca_service.config import AppConfig
from matoca_service.line.liff import LiffClient
from matoca_service.line.refresh import LineRefreshClient
from matoca_service.line.token_manager import TokenManager
from matoca_service.matoca.client import MatocaClient
from matoca_service.matoca.models import Shop, Waiting
from matoca_service.state.store import JsonStateStore


class DashboardData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant: str
    native_access_expires_at: datetime
    liff_expires_at: datetime
    page: int = 1
    shops: list[Shop]
    waiting: list[Waiting]


class MatocaService:
    def __init__(self, config_path: Path, state_path: Path) -> None:
        self._config = AppConfig.from_toml(config_path)
        self._store = JsonStateStore(state_path)
        self._operation_lock = asyncio.Lock()

    async def dashboard(
        self,
        merchant_key: str,
        keyword: str | None,
        page: int = 1,
    ) -> DashboardData:
        async with self._operation_lock:
            return await self._dashboard_unlocked(merchant_key, keyword, page)

    async def _dashboard_unlocked(
        self,
        merchant_key: str,
        keyword: str | None,
        page: int = 1,
    ) -> DashboardData:
        merchant = self._config.merchants[merchant_key]
        async with httpx.AsyncClient(http2=True, timeout=30) as http:
            manager = TokenManager(
                self._store,
                LineRefreshClient(self._config.line, http),
                liff_client=LiffClient(self._config.line, http),
            )
            native = await manager.ensure_native_token()

            async def fetch(*, force_liff: bool) -> tuple[datetime, list[Shop], list[Waiting]]:
                liff = await manager.ensure_liff_token(
                    liff_id=merchant.liff_id,
                    merchant=merchant,
                    force=force_liff,
                )
                matoca = MatocaClient(merchant, http, liff.access_token)
                await matoca.authenticate()
                shops, waiting = await asyncio.gather(
                    matoca.list_shops(page=page, keyword=keyword),
                    matoca.list_waiting(),
                )
                return liff.expires_at, shops, waiting

            try:
                liff_expires_at, shops, waiting = await fetch(force_liff=False)
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in {401, 403}:
                    raise
                liff_expires_at, shops, waiting = await fetch(force_liff=True)
        return DashboardData(
            merchant=merchant.name,
            native_access_expires_at=native.access_expires_at,
            liff_expires_at=liff_expires_at,
            page=page,
            shops=shops,
            waiting=waiting,
        )
