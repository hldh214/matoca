from typing import Any

import httpx

from matoca_service.config import MerchantConfig
from matoca_service.matoca.models import CreateWaitingRequest, Shop, Waiting


class MatocaApiError(RuntimeError):
    """Raised when Matoca rejects a request or returns an invalid envelope."""


class MatocaClient:
    MAX_SHOP_PAGES = 20

    def __init__(
        self,
        merchant: MerchantConfig,
        http: httpx.AsyncClient,
        access_token: str,
    ) -> None:
        self._merchant = merchant
        self._http = http
        self._access_token = access_token

    @property
    def _base_url(self) -> str:
        return str(self._merchant.api_base_url).rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._access_token}",
            "x-access-type": "mini",
            "accept": "application/json",
            "origin": str(self._merchant.origin).rstrip("/"),
            "referer": str(self._merchant.origin),
            "x-requested-with": "jp.naver.line.android",
        }

    async def _json(self, response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise MatocaApiError("Matoca returned an unsuccessful response")
        return payload

    async def authenticate(self) -> None:
        response = await self._http.post(
            f"{self._base_url}/liff/auth",
            headers=self._headers,
            json={
                "liff_id": self._merchant.liff_id,
                "access_token": self._access_token,
            },
        )
        await self._json(response)

    async def list_shops(
        self,
        *,
        page: int = 1,
        keyword: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
    ) -> list[Shop]:
        params: dict[str, str | int | float] = {"page": page}
        if keyword:
            params["keyword"] = keyword
        if lat is not None:
            params["lat"] = lat
        if lng is not None:
            params["lng"] = lng
        response = await self._http.get(
            f"{self._base_url}/liff/shops",
            headers=self._headers,
            params=params,
        )
        payload = await self._json(response)
        content = payload.get("content")
        if not isinstance(content, dict) or not isinstance(content.get("shops"), list):
            raise MatocaApiError("Matoca shops response has an invalid content shape")
        return [Shop.model_validate(shop) for shop in content["shops"]]

    async def list_waiting(self) -> list[Waiting]:
        response = await self._http.get(
            f"{self._base_url}/liff/waiting",
            headers=self._headers,
        )
        payload = await self._json(response)
        content = payload.get("content")
        if not isinstance(content, list):
            raise MatocaApiError("Matoca waiting response has an invalid content shape")
        return [Waiting.model_validate(waiting) for waiting in content]

    async def list_all_shops(self) -> list[Shop]:
        shops: list[Shop] = []
        for page in range(1, self.MAX_SHOP_PAGES + 1):
            page_shops = await self.list_shops(page=page)
            if not page_shops:
                break
            shops.extend(page_shops)
        return shops

    async def get_shop(self, shop_id: int) -> Shop:
        response = await self._http.get(
            f"{self._base_url}/liff/shops/{shop_id}",
            headers=self._headers,
        )
        payload = await self._json(response)
        content = payload.get("content")
        if not isinstance(content, dict) or not isinstance(content.get("shop"), dict):
            raise MatocaApiError("Matoca shop response has an invalid content shape")
        return Shop.model_validate(content["shop"])

    async def get_waiting(self, waiting_id: int) -> Waiting:
        response = await self._http.get(
            f"{self._base_url}/liff/waiting/{waiting_id}",
            headers=self._headers,
        )
        payload = await self._json(response)
        content = payload.get("content")
        if not isinstance(content, dict):
            raise MatocaApiError("Matoca waiting response has an invalid content shape")
        return Waiting.model_validate(content)

    async def create_waiting(self, request: CreateWaitingRequest) -> Waiting:
        response = await self._http.post(
            f"{self._base_url}/liff/waiting",
            headers=self._headers,
            json=request.model_dump(mode="json"),
        )
        payload = await self._json(response)
        content = payload.get("content")
        if not isinstance(content, dict):
            raise MatocaApiError("Matoca create waiting response has an invalid content shape")
        return Waiting.model_validate(content)

    async def cancel_waiting(self, waiting_id: int) -> None:
        response = await self._http.delete(
            f"{self._base_url}/liff/waiting/{waiting_id}",
            headers=self._headers,
        )
        await self._json(response)
