from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

from matoca_service.service import PartyPreferences

from .fake_service import BrowserFakeService

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

pytestmark = pytest.mark.browser


def open_sawayaka_console(page: Page, browser_base_url: str) -> None:
    from playwright.sync_api import expect

    page.goto(f"{browser_base_url}/merchants/sawayaka")
    expect(page.get_by_role("region", name="店舗一覧")).to_be_visible()
    expect(page.locator(".shop-row")).to_have_count(1)
    expect(page.get_by_text("現在の順番待ちはありません", exact=True)).to_be_visible()


def wait_for_dialog_ready(
    page: Page,
    dialog_name: str,
    form_selector: str,
) -> Locator:
    from playwright.sync_api import expect

    dialog = page.get_by_role("dialog", name=dialog_name)
    expect(dialog).to_be_visible()
    expect(dialog.locator(form_selector)).to_have_attribute("aria-busy", "false")
    return dialog


def test_selects_merchant_and_filters_shops(
    safe_page: Page,
    browser_base_url: str,
    assert_clean_browser: Callable[[], None],
) -> None:
    from playwright.sync_api import expect

    safe_page.goto(browser_base_url)
    expect(safe_page.get_by_text("炭焼きレストラン さわやか", exact=True)).to_be_visible()
    expect(safe_page.get_by_text("ラ・オハナ 横浜本牧", exact=True)).to_be_visible()

    safe_page.get_by_role("link", name=re.compile("炭焼きレストラン さわやか")).click()

    expect(safe_page).to_have_url(f"{browser_base_url}/merchants/sawayaka")
    expect(safe_page.get_by_role("button", name="受付可能")).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(safe_page.locator(".shop-row")).to_have_count(1)
    expect(safe_page.locator("#available-count")).to_have_text("1")
    expect(safe_page.locator("#total-count")).to_have_text("4")
    expect(safe_page.get_by_text("8組")).to_be_visible()
    expect(safe_page.get_by_text("25分")).to_be_visible()

    safe_page.get_by_role("button", name="すべて").click()
    expect(safe_page.locator(".shop-row")).to_have_count(4)
    safe_page.get_by_role("searchbox", name="店舗を検索").fill("休業")
    expect(safe_page.locator(".shop-row")).to_have_count(1)
    expect(safe_page.get_by_text("休業テスト店", exact=True)).to_be_visible()
    assert_clean_browser()


def test_saved_settings_apply_to_the_next_join_dialog(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
    assert_clean_browser: Callable[[], None],
) -> None:
    from playwright.sync_api import expect

    open_sawayaka_console(safe_page, browser_base_url)
    safe_page.get_by_role("button", name="今すぐ受付").click()
    join_dialog = wait_for_dialog_ready(safe_page, "浜松テスト店", "#join-form")
    expect(join_dialog.locator("#adult-count")).to_have_text("2")
    expect(join_dialog.locator("#child-count")).to_have_text("0")
    join_dialog.get_by_role("button", name="閉じる").click()
    expect(join_dialog).to_be_hidden()

    safe_page.get_by_role("button", name="設定").click()
    settings_dialog = wait_for_dialog_ready(safe_page, "設定", "#settings-form")
    settings_dialog.get_by_role("button", name="大人を増やす").click()
    settings_dialog.get_by_role("button", name="子どもを増やす").click()
    expect(settings_dialog.locator("#settings-adult-count")).to_have_text("3")
    expect(settings_dialog.locator("#settings-child-count")).to_have_text("1")
    settings_dialog.get_by_role("button", name="設定を保存").click()
    expect(settings_dialog).to_be_hidden()

    assert browser_service.preferences == PartyPreferences(
        default_adult_count=3,
        default_child_count=1,
    )

    safe_page.get_by_role("button", name="今すぐ受付").click()
    join_dialog = wait_for_dialog_ready(safe_page, "浜松テスト店", "#join-form")
    expect(join_dialog.locator("#adult-count")).to_have_text("3")
    expect(join_dialog.locator("#child-count")).to_have_text("1")
    join_dialog.get_by_role("button", name="閉じる").click()
    assert_clean_browser()


def test_joins_and_cancels_a_synthetic_queue(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
    assert_clean_browser: Callable[[], None],
) -> None:
    from playwright.sync_api import expect

    open_sawayaka_console(safe_page, browser_base_url)
    safe_page.get_by_role("button", name="すべて").click()
    expect(safe_page.locator(".shop-row")).to_have_count(4)
    safe_page.get_by_role("button", name="今すぐ受付").click()
    join_dialog = wait_for_dialog_ready(safe_page, "浜松テスト店", "#join-form")

    join_dialog.get_by_role("button", name="大人を増やす").click()
    join_dialog.get_by_role("button", name="子どもを増やす").click()
    confirmation = join_dialog.get_by_role("combobox", name="注意事項を確認しましたか")
    expect(confirmation).to_have_value("1")
    expect(confirmation).to_be_enabled()
    join_dialog.get_by_role("button", name="この内容で順番待ちを申し込む").click()

    expect(join_dialog).to_be_hidden()
    assert browser_service.submissions[-1].model_dump() == {
        "shop_id": 3272,
        "adult_count": 3,
        "child_count": 1,
        "answer1": 1,
        "answer2": None,
        "in_advance_information": "",
    }
    queue_band = safe_page.get_by_role("region", name="現在の順番待ち")
    expect(queue_band.get_by_text("101", exact=True)).to_be_visible()
    expect(safe_page.locator(".join-button:enabled")).to_have_count(0)

    queue_band.get_by_role("button", name="取消").click()
    cancel_dialog = safe_page.get_by_role("dialog", name="順番待ちを取り消しますか")
    expect(cancel_dialog).to_be_visible()
    cancel_dialog.get_by_role("button", name="順番待ちを取り消す").click()

    expect(cancel_dialog).to_be_hidden()
    expect(safe_page.get_by_text("現在の順番待ちはありません", exact=True)).to_be_visible()
    expect(safe_page.get_by_role("button", name="今すぐ受付")).to_be_enabled()
    assert_clean_browser()


def test_manual_refresh_is_distinct_from_automatic_reads(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
    assert_clean_browser: Callable[[], None],
) -> None:
    from playwright.sync_api import expect

    safe_page.clock.install()
    open_sawayaka_console(safe_page, browser_base_url)
    assert browser_service.console_reads == 1
    assert browser_service.waiting_reads == 1

    refresh = safe_page.get_by_role("button", name="最新情報に更新")
    refresh.click()
    expect(refresh).to_have_attribute("aria-busy", "false")
    assert browser_service.refresh_calls == 1
    reads_after_manual_refresh = (
        browser_service.console_reads,
        browser_service.waiting_reads,
    )

    with (
        safe_page.expect_response(re.compile(r"/api/merchants/sawayaka/console$")),
        safe_page.expect_response(re.compile(r"/api/merchants/sawayaka/waiting$")),
    ):
        safe_page.clock.run_for(30_000)

    assert browser_service.console_reads > reads_after_manual_refresh[0]
    assert browser_service.waiting_reads > reads_after_manual_refresh[1]
    assert browser_service.refresh_calls == 1
    assert_clean_browser()
