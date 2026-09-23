from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from matoca_service.matoca.models import Waiting

if TYPE_CHECKING:
    from playwright.sync_api import Page

    from .fake_service import BrowserFakeService

pytestmark = pytest.mark.browser


def test_other_merchant_ticket_is_visible_and_cancel_uses_its_merchant(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from playwright.sync_api import expect

    browser_service._waiting["sawayaka"] = [
        Waiting(id=101, shop_id=3272, number=87, count=12, adult_count=2, child_count=0)
    ]
    safe_page.goto(f"{browser_base_url}/merchants/la_ohana_yokohamahonmoku")
    panel = safe_page.get_by_role("region", name="現在の順番待ち")
    expect(panel.get_by_text("87", exact=True)).to_be_visible()
    expect(panel).to_contain_text("大人2人・子ども0人")
    expect(panel.get_by_role("link", name="受付中の店舗へ")).to_have_attribute(
        "href", "/merchants/sawayaka"
    )
    expect(panel.get_by_role("link", name="地図で見る")).to_have_attribute(
        "href", re.compile(r"https://www.google.com/maps/search/.*")
    )
    panel.get_by_role("button", name="取消", exact=True).click()
    with safe_page.expect_response(
        lambda r: (
            r.request.method == "DELETE" and r.url.endswith("/api/merchants/sawayaka/waiting/101")
        )
    ) as response:
        safe_page.get_by_role("button", name="順番待ちを取り消す", exact=True).click()
    assert response.value.status == 204
    expect(safe_page.get_by_role("status", name="操作結果")).to_contain_text("取り消しました")


def test_refresh_updates_queue_and_favorite_filter_survives_reload(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from playwright.sync_api import expect

    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    expect(safe_page.get_by_text("現在の順番待ちはありません", exact=True)).to_be_visible()

    browser_service._waiting["sawayaka"] = [Waiting(id=101, shop_id=3272, number=87, count=12)]
    safe_page.get_by_role("button", name="最新情報に更新").click()
    expect(safe_page.locator("#current-queue")).to_contain_text("87")
    safe_page.get_by_role("button", name="お気に入りに追加").click()
    safe_page.get_by_role("button", name="お気に入り", exact=True).click()
    safe_page.get_by_role("searchbox").fill("浜松")
    safe_page.get_by_label("並び順").select_option("name")
    safe_page.reload()
    expect(safe_page.get_by_role("button", name="お気に入り", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(safe_page.get_by_role("searchbox")).to_have_value("浜松")
    expect(safe_page.get_by_label("並び順")).to_have_value("name")
    expect(safe_page.locator(".shop-row")).to_have_count(1)


def test_favorite_failure_is_visible_and_does_not_change_saved_favorites(
    safe_page: Page, browser_base_url: str
) -> None:
    from playwright.sync_api import expect

    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.route("**/favorite", lambda r: r.fulfill(status=200, body="not-json"))
    safe_page.get_by_role("button", name="お気に入りに追加").click()
    expect(safe_page.get_by_role("status", name="操作結果")).to_contain_text("保存できません")
    expect(safe_page.get_by_role("button", name="お気に入りに追加")).to_be_enabled()
    safe_page.get_by_role("button", name="お気に入り", exact=True).click()
    expect(safe_page.locator(".shop-row")).to_have_count(0)


def test_homepage_keeps_ticket_on_failed_refresh_and_can_retry(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from playwright.sync_api import expect

    browser_service._waiting["sawayaka"] = [Waiting(id=101, shop_id=3272, number=87, count=12)]
    safe_page.goto(browser_base_url)
    expect(safe_page.locator("#personal-queues")).to_contain_text("87")
    safe_page.route("**/api/queues", lambda r: r.fulfill(status=200, body="not-json"))
    safe_page.get_by_role("button", name="順番待ちを更新").click()
    expect(safe_page.locator("#personal-queue-error")).to_contain_text("更新できません")
    expect(safe_page.locator("#personal-queues")).to_contain_text("87")
    safe_page.unroute("**/api/queues")
    safe_page.get_by_role("button", name="順番待ちを更新").click()
    expect(safe_page.locator("#personal-queue-error")).to_be_empty()


def test_merchant_keeps_ticket_when_queue_response_is_not_a_list(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from playwright.sync_api import expect

    browser_service._waiting["sawayaka"] = [Waiting(id=101, shop_id=3272, number=87, count=12)]
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    expect(safe_page.locator("#current-queue")).to_contain_text("87")
    safe_page.route("**/api/queues", lambda r: r.fulfill(status=200, json={}))
    safe_page.get_by_role("button", name="最新情報に更新").click()
    expect(safe_page.locator("#current-queue")).to_contain_text("前回の情報を表示")
    expect(safe_page.locator("#current-queue")).to_contain_text("87")
    safe_page.unroute("**/api/queues")
    safe_page.get_by_role("button", name="再読み込み").click()
    expect(safe_page.locator("#current-queue")).not_to_contain_text("前回の情報を表示")


def test_retired_automation_intent_can_be_finished_from_ticket(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from datetime import UTC, datetime

    from playwright.sync_api import expect

    from matoca_service.tracking.models import QueueIntentSummary

    browser_service.queue_intents = [
        QueueIntentSummary(
            intent_id="retired",
            merchant_key="sawayaka",
            shop_id=3272,
            shop_name="浜松テスト店",
            submitted_at=datetime(2026, 9, 23, tzinfo=UTC),
            official_minutes_at_submission=30,
            official_is_more_at_submission=False,
            adult_count=2,
            child_count=0,
            source="automation",
            status="unresolved",
        )
    ]
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="受付結果を確認して終了", exact=True).click()
    safe_page.locator("#resolve-intent-consent").check()
    with safe_page.expect_response("**/api/queues/intents/retired/resolve") as response:
        safe_page.get_by_role("button", name="受付がないことを確認して終了", exact=True).click()
    assert response.value.status == 204
    expect(safe_page.get_by_text("現在の順番待ちはありません", exact=True)).to_be_visible()
