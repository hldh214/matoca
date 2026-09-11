from datetime import UTC, datetime

import pytest

from matoca_service.console import console_item
from matoca_service.matoca.models import Shop, ShopForms, WaitingEstimate
from matoca_service.service import MatocaService
from matoca_service.storage.database import Database
from matoca_service.storage.models import CollectionWrite, ShopObservation
from matoca_service.storage.repositories import ShopRepository


@pytest.mark.parametrize(
    ("shop", "detail_fresh", "status", "label", "can_join"),
    [
        (
            Shop(id=1, name="A", is_open=True, is_issuable=True),
            True,
            "available",
            "受付可能",
            True,
        ),
        (
            Shop(id=1, name="A", is_open=False, is_issuable=False),
            True,
            "closed",
            "営業時間外",
            False,
        ),
        (Shop(id=1, name="A", is_holiday=True), True, "holiday", "休業", False),
        (
            Shop(id=1, name="A", is_open=True, is_suspended=True),
            True,
            "suspended",
            "受付停止",
            False,
        ),
        (
            Shop(id=1, name="A", is_open=True, is_issuable=True),
            False,
            "stale",
            "更新待ち",
            False,
        ),
    ],
)
def test_console_resolves_shop_status(
    shop: Shop,
    detail_fresh: bool,
    status: str,
    label: str,
    can_join: bool,
) -> None:
    item = console_item(shop, detail_fresh=detail_fresh)

    assert (item.status, item.status_label, item.can_join) == (status, label, can_join)


def test_console_item_preserves_official_observation_without_prediction_fields() -> None:
    observed_at = datetime(2026, 9, 10, 8, 15, tzinfo=UTC)
    forms = ShopForms(min_adult=1, max_adult=6)

    item = console_item(
        Shop(
            id=1,
            name="A",
            current_waiting=12,
            waiting_time=WaitingEstimate(minutes=45, is_more=True),
            forms=forms,
            is_open=True,
            is_issuable=True,
        ),
        detail_fresh=True,
        observed_at=observed_at,
    )

    assert item.current_waiting == 12
    assert item.official_waiting_minutes == 45
    assert item.official_waiting_is_more is True
    assert item.forms == forms
    assert item.updated_at == observed_at
    assert "prediction" not in item.model_dump()


@pytest.mark.asyncio
async def test_merchant_console_reads_only_the_stored_sqlite_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line_client_path = tmp_path / "line_client.toml"
    line_client_path.write_text(
        "\n".join(
            [
                'host = "legy-jp.line-apps.com"',
                'application = "synthetic-app"',
                'locale = "ja_JP"',
                'protocol_version = "1"',
                'user_agent = "synthetic-agent"',
            ]
        ),
        encoding="utf-8",
    )
    database = Database(tmp_path / "matoca.db")
    database.initialize()
    ShopRepository(database).save_cycle(
        CollectionWrite(
            merchant_key="sawayaka",
            observed_at=datetime(2026, 9, 10, 8, tzinfo=UTC),
            shops=[
                ShopObservation(
                    shop=Shop(
                        id=3272,
                        name="Synthetic Shop",
                        is_open=True,
                        is_issuable=True,
                    ),
                    list_fresh=True,
                    detail_fresh=True,
                )
            ],
        )
    )
    service = MatocaService(line_client_path, tmp_path / "state.json", database.path)

    async def unexpected(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("cached console must not access Matoca or collection")

    monkeypatch.setattr(service, "_authenticated_read", unexpected)
    monkeypatch.setattr(service, "read_collection_cycle", unexpected)

    console = await service.merchant_console("sawayaka")

    assert console.shops[0].id == 3272
    assert console.shops[0].status_label == "受付可能"
