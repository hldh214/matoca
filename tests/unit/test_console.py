from datetime import UTC, datetime, time, timedelta, tzinfo

import pytest

from matoca_service.console import MerchantSummary, build_console, console_item
from matoca_service.matoca.models import Shop, ShopForms, WaitingEstimate
from matoca_service.service import MatocaService
from matoca_service.storage.database import Database
from matoca_service.storage.models import (
    CatalogState,
    CollectionWrite,
    MerchantPollState,
    PollWindow,
    ShopObservation,
    StoredShop,
)
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


@pytest.mark.parametrize(
    ("catalog_age", "detail_age", "expected_stale"),
    [
        (timedelta(minutes=10), timedelta(minutes=10), False),
        (timedelta(minutes=11), timedelta(minutes=10), True),
        (timedelta(minutes=10), timedelta(minutes=11), True),
    ],
)
def test_console_reports_catalog_staleness_without_overriding_fresh_detail(
    catalog_age: timedelta,
    detail_age: timedelta,
    expected_stale: bool,
) -> None:
    now = datetime(2026, 9, 11, 8, tzinfo=UTC)
    shop = Shop(id=1, name="A", is_open=True, is_issuable=True)
    stored_shop = StoredShop(
        merchant_key="sawayaka",
        shop=shop,
        observation=ShopObservation(
            shop=shop,
            list_fresh=True,
            detail_fresh=True,
            observed_at=now - detail_age,
        ),
        last_detail_at=now - detail_age,
    )

    console = build_console(
        MerchantSummary(key="sawayaka", name="Synthetic Merchant"),
        [stored_shop],
        CatalogState(observed_at=now - catalog_age, complete=True),
        MerchantPollState(merchant_key="sawayaka"),
        now,
        stale_after=timedelta(minutes=10),
    )

    assert console.stale is expected_stale
    assert (console.shops[0].status, console.shops[0].status_label, console.shops[0].can_join) == (
        ("stale", "更新待ち", False)
        if detail_age > timedelta(minutes=10)
        else ("available", "受付可能", True)
    )


@pytest.mark.parametrize("stale_reason", ["failed_detail", "expired_detail", "missing_detail"])
def test_mixed_console_keeps_fresh_shops_joinable(stale_reason: str) -> None:
    now = datetime(2026, 9, 11, 8, tzinfo=UTC)
    fresh = Shop(
        id=1,
        name="Fresh",
        is_open=True,
        is_issuable=True,
        waiting_time=WaitingEstimate(minutes=15),
    )
    stale = fresh.model_copy(update={"id": 2, "name": "Stale"})
    stale_at = now - timedelta(minutes=11) if stale_reason == "expired_detail" else now
    console = build_console(
        MerchantSummary(key="sawayaka", name="Synthetic Merchant"),
        [
            StoredShop("sawayaka", fresh, ShopObservation(fresh, True, True, observed_at=now), now),
            StoredShop(
                "sawayaka",
                stale,
                None
                if stale_reason == "missing_detail"
                else ShopObservation(
                    stale,
                    True,
                    stale_reason != "failed_detail",
                    observed_at=stale_at,
                ),
                None if stale_reason == "missing_detail" else stale_at,
            ),
        ],
        CatalogState(observed_at=now, complete=True),
        MerchantPollState(merchant_key="sawayaka"),
        now,
        stale_after=timedelta(minutes=10),
    )

    assert console.stale is True
    assert (console.available_count, console.total_count) == (1, 2)
    assert [
        (item.status, item.can_join, item.official_waiting_minutes) for item in console.shops
    ] == [
        ("available", True, 15),
        ("stale", False, None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age", "expected_status", "expected_label", "expected_stale"),
    [
        (timedelta(minutes=2), "available", "受付可能", False),
        (timedelta(minutes=3), "stale", "更新待ち", True),
    ],
)
async def test_merchant_console_uses_sqlite_and_current_poll_interval_for_staleness(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    age: timedelta,
    expected_status: str,
    expected_label: str,
    expected_stale: bool,
) -> None:
    now = datetime(2026, 9, 11, 8, tzinfo=UTC)
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
            observed_at=now - age,
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
    monkeypatch.setattr(
        service._shops,
        "poll_window",
        lambda merchant_key, at: PollWindow(start=time(16, 30), end=time(17, 30)),
    )

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            del cls
            return now if tz is None else now.astimezone(tz)

    monkeypatch.setattr("matoca_service.service.datetime", FixedDatetime)

    console = await service.merchant_console("sawayaka")

    assert console.shops[0].id == 3272
    assert console.stale is expected_stale
    assert (console.shops[0].status, console.shops[0].status_label) == (
        expected_status,
        expected_label,
    )
