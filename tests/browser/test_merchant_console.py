from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from matoca_service.service import PartyPreferences
from matoca_service.tracking.models import QueueIntentSummary

from .fake_service import BrowserFakeService

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page, Route

pytestmark = pytest.mark.browser
TEST_CLOCK_START = datetime(2026, 9, 12, 12)
TEST_CLOCK_PAUSED = datetime(2026, 9, 12, 12, 0, 1)


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
    expect(safe_page.get_by_text("予測 20〜30分・信頼度 中・実効7.5件")).to_be_visible()

    safe_page.get_by_role("button", name="すべて").click()
    expect(safe_page.locator(".shop-row")).to_have_count(4)
    safe_page.get_by_role("searchbox", name="店舗を検索").fill("休業")
    expect(safe_page.locator(".shop-row")).to_have_count(1)
    expect(safe_page.get_by_text("休業テスト店", exact=True)).to_be_visible()
    assert_clean_browser()


def test_homepage_reload_renders_unresolved_submission_without_observations(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
    assert_clean_browser: Callable[[], None],
) -> None:
    from playwright.sync_api import expect

    browser_service.queue_intents = [
        QueueIntentSummary(
            intent_id="intent-1",
            merchant_key="sawayaka",
            merchant_name="炭焼きレストラン さわやか",
            shop_id=3272,
            shop_name="浜松テスト店",
            submitted_at=datetime(2026, 9, 14, 8, 30, tzinfo=UTC),
            official_minutes_at_submission=25,
            official_is_more_at_submission=False,
            adult_count=2,
            child_count=0,
            source="manual",
            status="unresolved",
            error_code="send_outcome_unknown",
        )
    ]

    safe_page.goto(browser_base_url)
    safe_page.reload()

    queue_band = safe_page.get_by_role("region", name="あなたの順番待ち")
    expect(queue_band.get_by_text("炭焼きレストラン さわやか・浜松テスト店")).to_be_visible()
    expect(
        queue_band.get_by_text(re.compile("受付結果を確認できません.*更新 17:30"))
    ).to_be_visible()
    assert_clean_browser()


def test_sorts_favorites_and_opens_shop_history(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
    assert_clean_browser: Callable[[], None],
) -> None:
    from playwright.sync_api import expect

    open_sawayaka_console(safe_page, browser_base_url)
    safe_page.get_by_role("button", name="すべて").click()
    safe_page.get_by_label("並び順").select_option("official")
    rows = safe_page.locator(".shop-row")
    expect(rows.nth(0)).to_contain_text("浜松テスト店")
    expect(rows.nth(1)).to_contain_text("休業テスト店")
    expect(rows.nth(2).locator(".metric").nth(1).locator("strong")).to_have_text("—")

    favorite = safe_page.locator('.shop-row[data-id="3274"]').get_by_role(
        "button", name="お気に入りに追加"
    )
    favorite.click()
    expect(rows.nth(0)).to_contain_text("受付停止テスト店")
    assert browser_service.favorite_ids == {3274}

    safe_page.reload()
    safe_page.get_by_role("button", name="すべて").click()
    expect(safe_page.locator(".shop-row").nth(0)).to_contain_text("受付停止テスト店")
    safe_page.locator('.shop-row[data-id="3274"]').get_by_role("button", name="履歴を見る").click()
    dialog = safe_page.get_by_role("dialog", name="受付停止テスト店の履歴")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_label("日付")).to_have_value("2026-09-10")
    expect(dialog.get_by_text("待ち組数（組）", exact=True)).to_be_visible()  # noqa: RUF001
    expect(dialog.get_by_text("公式待ち時間（分）", exact=True)).to_be_visible()  # noqa: RUF001
    expect(dialog.get_by_text("通常予測（分）", exact=True)).to_be_visible()  # noqa: RUF001
    expect(dialog.locator("svg")).to_have_count(3)
    expect(dialog.locator("path.history-line")).to_have_count(6)
    dialog.get_by_label("日付").fill("2026-09-09")
    expect(dialog.get_by_text("この日の記録はありません")).to_be_visible()
    assert_clean_browser()


@pytest.mark.parametrize("timezone_id", ["America/Los_Angeles"])
def test_history_uses_tokyo_day_axis_and_client_timezone_labels(
    safe_page: Page,
    browser_base_url: str,
    timezone_id: str,
    assert_clean_browser: Callable[[], None],
) -> None:
    from playwright.sync_api import expect

    del timezone_id
    open_sawayaka_console(safe_page, browser_base_url)
    safe_page.locator('.shop-row[data-id="3272"] .history-button').click()
    dialog = safe_page.get_by_role("dialog", name="浜松テスト店の履歴")
    expect(dialog.get_by_text("表示時刻: America/Los_Angeles")).to_be_visible()
    first_path = dialog.locator("path.history-line").first.get_attribute("d")
    assert first_path is not None and first_path.startswith("M279.")
    expect(dialog.locator(".history-lower-bound")).to_have_count(1)
    expect(dialog.get_by_text("以上を示す点があります")).to_be_visible()

    dialog.get_by_label("日付").fill("2026-09-09")
    expect(dialog.get_by_text("静岡県浜松市テスト町1-1")).to_be_visible()
    expect(dialog.get_by_text("この日の記録はありません")).to_be_visible()
    assert_clean_browser()


def test_favorite_button_waits_for_persistence_before_another_toggle(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
) -> None:
    from playwright.sync_api import expect

    browser_service.favorite_delay_seconds = 0.3
    open_sawayaka_console(safe_page, browser_base_url)
    button = safe_page.locator('.shop-row[data-id="3272"]').get_by_label("お気に入りに追加")
    button.click()
    pending = safe_page.locator('.shop-row[data-id="3272"]').get_by_label("お気に入りから削除")
    expect(pending).to_be_disabled()
    pending.dispatch_event("click")
    expect(pending).to_be_enabled(timeout=2_000)
    assert browser_service.favorite_writes == [(3272, True)]


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
    expect(join_dialog.locator("#child-count")).to_have_text("1")
    join_dialog.get_by_role("button", name="閉じる").click()
    expect(join_dialog).to_be_hidden()

    safe_page.get_by_role("button", name="設定", exact=True).click()
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

    safe_page.clock.install(time=TEST_CLOCK_START)
    open_sawayaka_console(safe_page, browser_base_url)
    safe_page.clock.pause_at(TEST_CLOCK_PAUSED)
    waiting_url = f"{browser_base_url}/api/merchants/sawayaka/waiting"
    queues_url = f"{browser_base_url}/api/queues"
    response_events: list[tuple[str, str]] = []
    safe_page.on(
        "response",
        lambda response: response_events.append((response.request.method, response.url)),
    )
    assert browser_service.waiting_reads == 0
    safe_page.get_by_role("button", name="すべて").click()
    expect(safe_page.locator(".shop-row")).to_have_count(4)
    safe_page.get_by_role("button", name="今すぐ受付").click()
    join_dialog = wait_for_dialog_ready(safe_page, "浜松テスト店", "#join-form")

    for counter, label, minimum, maximum, submitted in [
        ("adult", "大人", 2, 5, 3),
        ("child", "子ども", 1, 3, 1),
    ]:
        count = join_dialog.locator(f"#{counter}-count")
        decrease = join_dialog.get_by_role("button", name=f"{label}を減らす")
        increase = join_dialog.get_by_role("button", name=f"{label}を増やす")
        expect(count).to_have_text(str(minimum))
        for value in range(minimum + 1, maximum + 1):
            expect(increase).to_be_enabled()
            increase.click()
            expect(count).to_have_text(str(value))
        expect(increase).to_be_disabled()
        increase.dispatch_event("click")
        expect(count).to_have_text(str(maximum))
        for value in range(maximum - 1, minimum - 1, -1):
            expect(decrease).to_be_enabled()
            decrease.click()
            expect(count).to_have_text(str(value))
        expect(decrease).to_be_disabled()
        decrease.dispatch_event("click")
        expect(count).to_have_text(str(minimum))
        for _ in range(submitted - minimum):
            increase.click()
        expect(count).to_have_text(str(submitted))
    confirmation = join_dialog.get_by_role("combobox", name="注意事項を確認しましたか")
    expect(confirmation).to_have_value("1")
    expect(confirmation).to_be_enabled()
    event_offset = len(response_events)
    with (
        safe_page.expect_response(
            lambda response: response.url == waiting_url and response.request.method == "POST"
        ) as join_response_info,
        safe_page.expect_request_finished(
            lambda request: request.url == queues_url and request.method == "GET",
            timeout=5_000,
        ) as join_reload_info,
    ):
        join_dialog.get_by_role("button", name="この内容で順番待ちを申し込む").click()

    assert join_response_info.value.status == 200
    assert join_reload_info.value.method == "GET"
    assert [
        event for event in response_events[event_offset:] if event[1] in {waiting_url, queues_url}
    ] == [
        ("POST", waiting_url),
        ("GET", queues_url),
    ]
    assert browser_service.waiting_reads == 0
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
    expect(queue_band.locator(".queue-metrics span").last).to_have_text(
        "残り予測20〜30分信頼度 中・実効7.5件"
    )
    join_actions = safe_page.get_by_role("button", name="順番待ち受付中", exact=True)
    expect(join_actions).to_have_count(4)
    for action in join_actions.all():
        expect(action).to_be_disabled()

    queue_band.get_by_role("button", name="取消").click()
    cancel_dialog = safe_page.get_by_role("dialog", name="順番待ちを取り消しますか")
    expect(cancel_dialog).to_be_visible()
    cancel_url = f"{waiting_url}/900000001"
    event_offset = len(response_events)
    with (
        safe_page.expect_response(
            lambda response: response.url == cancel_url and response.request.method == "DELETE"
        ) as cancel_response_info,
        safe_page.expect_request_finished(
            lambda request: request.url == queues_url and request.method == "GET",
            timeout=5_000,
        ) as cancel_reload_info,
    ):
        cancel_dialog.get_by_role("button", name="順番待ちを取り消す").click()

    assert cancel_response_info.value.status == 204
    assert cancel_reload_info.value.method == "GET"
    assert [
        event for event in response_events[event_offset:] if event[1] in {queues_url, cancel_url}
    ] == [("DELETE", cancel_url), ("GET", queues_url)]
    assert browser_service.waiting_reads == 0
    expect(cancel_dialog).to_be_hidden()
    expect(safe_page.get_by_text("現在の順番待ちはありません", exact=True)).to_be_visible()
    expect(safe_page.get_by_role("button", name="今すぐ受付")).to_be_enabled()
    assert_clean_browser()


def test_zero_count_create_remains_called_when_followup_queue_read_fails(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
    assert_clean_browser: Callable[[], None],
) -> None:
    from playwright.sync_api import expect

    browser_service.create_count = 0
    open_sawayaka_console(safe_page, browser_base_url)

    def fail_followup_queue_read(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json", body="not-json")

    safe_page.route("**/api/queues", fail_followup_queue_read)
    safe_page.get_by_role("button", name="今すぐ受付").click()
    join_dialog = wait_for_dialog_ready(safe_page, "浜松テスト店", "#join-form")
    join_dialog.get_by_role("button", name="この内容で順番待ちを申し込む").click()

    expect(join_dialog).to_be_hidden()
    queue_band = safe_page.get_by_role("region", name="現在の順番待ち")
    expect(queue_band.get_by_text("呼び出し済み", exact=True)).to_be_visible()
    expect(queue_band.get_by_role("button", name="取消")).to_have_count(0)
    assert_clean_browser()


def test_manual_refresh_is_distinct_from_automatic_reads(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
    assert_clean_browser: Callable[[], None],
) -> None:
    from playwright.sync_api import expect

    safe_page.clock.install(time=TEST_CLOCK_START)
    open_sawayaka_console(safe_page, browser_base_url)
    safe_page.clock.pause_at(TEST_CLOCK_PAUSED)
    assert browser_service.console_reads == 1
    assert browser_service.queue_reads == 1

    refresh = safe_page.get_by_role("button", name="最新情報に更新")
    refresh.click()
    expect(refresh).to_have_attribute("aria-busy", "false")
    assert browser_service.refresh_calls == 1
    reads_after_manual_refresh = (
        browser_service.console_reads,
        browser_service.queue_reads,
    )

    with (
        safe_page.expect_response(re.compile(r"/api/merchants/sawayaka/console$")),
        safe_page.expect_response(re.compile(r"/api/queues$")),
    ):
        safe_page.clock.run_for(30_000)

    assert browser_service.console_reads > reads_after_manual_refresh[0]
    assert browser_service.queue_reads > reads_after_manual_refresh[1]
    assert browser_service.waiting_reads == 0
    assert browser_service.refresh_calls == 1
    assert_clean_browser()
