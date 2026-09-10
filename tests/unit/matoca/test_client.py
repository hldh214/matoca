import httpx
import pytest
import respx
from pydantic import ValidationError

from matoca_service.config import MerchantConfig
from matoca_service.matoca.client import MatocaApiError, MatocaClient


@pytest.fixture
def merchant() -> MerchantConfig:
    return MerchantConfig(
        name="Sawayaka",
        liff_id="2006055787-m6P6OJ38",
        api_base_url="https://admin.junbanmachi.jp",
        origin="https://exclusive-mini.junbanmachi.jp",
        entry_url="https://exclusive-mini.junbanmachi.jp/sawayaka/",
        line_entry_url="line://app/2006055787-m6P6OJ38",
    )


@pytest.mark.asyncio
@respx.mock
async def test_auth_and_shops_reuse_the_same_liff_bearer(merchant: MerchantConfig) -> None:
    auth_route = respx.post("https://admin.junbanmachi.jp/liff/auth").mock(
        return_value=httpx.Response(200, json={"status": "success", "code": 200})
    )
    shops_route = respx.get("https://admin.junbanmachi.jp/liff/shops").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "code": 200,
                "content": {
                    "shops": [
                        {
                            "id": 3278,
                            "name": "炭焼きレストラン さわやか",
                            "sub_name": "イオンモール浜松市野店",
                            "current_waiting": 3,
                            "is_holiday": False,
                            "is_suspended": False,
                        }
                    ]
                },
            },
        )
    )

    async with httpx.AsyncClient() as http:
        client = MatocaClient(merchant, http, "liff-secret")
        await client.authenticate()
        shops = await client.list_shops(keyword="さわやか")

    assert auth_route.calls[0].request.headers["authorization"] == "Bearer liff-secret"
    assert shops_route.calls[0].request.headers["authorization"] == "Bearer liff-secret"
    assert auth_route.calls[0].request.read().decode().count("liff-secret") == 1
    assert shops[0].id == 3278
    assert shops[0].current_waiting == 3


@pytest.mark.asyncio
@respx.mock
async def test_waiting_returns_content_list(merchant: MerchantConfig) -> None:
    respx.get("https://admin.junbanmachi.jp/liff/waiting").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "code": 200, "content": []},
        )
    )

    async with httpx.AsyncClient() as http:
        client = MatocaClient(merchant, http, "liff-secret")
        waiting = await client.list_waiting()

    assert waiting == []


@pytest.mark.asyncio
@respx.mock
async def test_get_shop_reads_content_shop(merchant: MerchantConfig) -> None:
    route = respx.get("https://admin.junbanmachi.jp/liff/shops/3272").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "code": 200,
                "content": {
                    "shop": {
                        "id": 3272,
                        "name": "synthetic merchant",
                        "waiting_time": {"minutes": 90, "is_more": True},
                    }
                },
            },
        )
    )

    async with httpx.AsyncClient() as http:
        shop = await MatocaClient(merchant, http, "synthetic-liff").get_shop(3272)

    assert route.calls[0].request.headers["authorization"] == "Bearer synthetic-liff"
    assert shop.id == 3272
    assert shop.waiting_time is not None
    assert shop.waiting_time.minutes == 90


@pytest.mark.asyncio
@respx.mock
async def test_waiting_parses_non_empty_content(merchant: MerchantConfig) -> None:
    respx.get("https://admin.junbanmachi.jp/liff/waiting").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "code": 200,
                "content": [{"id": 125000001, "count": 72, "number": 87}],
            },
        )
    )

    async with httpx.AsyncClient() as http:
        waiting = await MatocaClient(merchant, http, "synthetic-liff").list_waiting()

    assert waiting[0].id == 125000001
    assert waiting[0].count == 72
    assert waiting[0].number == 87


@pytest.mark.asyncio
@respx.mock
async def test_waiting_rejects_non_numeric_count(merchant: MerchantConfig) -> None:
    respx.get("https://admin.junbanmachi.jp/liff/waiting").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "code": 200,
                "content": [{"id": 125000001, "count": "not-a-number"}],
            },
        )
    )

    async with httpx.AsyncClient() as http:
        with pytest.raises(ValidationError):
            await MatocaClient(merchant, http, "synthetic-liff").list_waiting()


@pytest.mark.asyncio
@respx.mock
async def test_get_waiting_reads_content_object(merchant: MerchantConfig) -> None:
    respx.get("https://admin.junbanmachi.jp/liff/waiting/125000001").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "code": 200,
                "content": {"id": 125000001, "count": 72, "number": 87},
            },
        )
    )

    async with httpx.AsyncClient() as http:
        waiting = await MatocaClient(merchant, http, "synthetic-liff").get_waiting(125000001)

    assert waiting.id == 125000001


@pytest.mark.asyncio
@respx.mock
async def test_get_shop_rejects_missing_shop_object(merchant: MerchantConfig) -> None:
    respx.get("https://admin.junbanmachi.jp/liff/shops/3272").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "code": 200, "content": {"shop": None}},
        )
    )

    async with httpx.AsyncClient() as http:
        with pytest.raises(
            MatocaApiError,
            match="Matoca shop response has an invalid content shape",
        ):
            await MatocaClient(merchant, http, "synthetic-liff").get_shop(3272)


@pytest.mark.asyncio
@respx.mock
async def test_get_waiting_rejects_non_object_content(merchant: MerchantConfig) -> None:
    respx.get("https://admin.junbanmachi.jp/liff/waiting/125000001").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "code": 200, "content": []},
        )
    )

    async with httpx.AsyncClient() as http:
        with pytest.raises(
            MatocaApiError,
            match="Matoca waiting response has an invalid content shape",
        ):
            await MatocaClient(merchant, http, "synthetic-liff").get_waiting(125000001)
