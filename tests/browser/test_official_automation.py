# Japanese UI labels intentionally use full-width punctuation.
# ruff: noqa: RUF001
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page

    from .fake_service import BrowserFakeService

pytestmark = pytest.mark.browser


def test_arrival_defaults_to_live_official_estimate(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from playwright.sync_api import expect

    browser_service._available_shop.waiting_time.minutes = 240
    safe_page.clock.set_fixed_time("2026-09-24T10:15:00+09:00")
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="自動受付", exact=True).click()
    expect(safe_page.locator("#join-status")).to_contain_text("約240分")
    expect(safe_page.get_by_label("到着予定", exact=True)).to_have_value(
        arrival_value(safe_page, 4)
    )
    assert browser_service.automation_requests == []


def arrival_value(page: Page, hours: int = 1) -> str:
    return page.evaluate(
        """hours => {
      const date = new Date(Date.now() + hours * 3600000);
      return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0,16);
    }""",
        hours,
    )


@pytest.mark.parametrize("viewport", [(1440, 900), (390, 844), (320, 568)])
def test_official_task_create_reload_edit_and_stop(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
    viewport: tuple[int, int],
) -> None:
    from playwright.sync_api import expect

    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="自動受付", exact=True).click()
    dialog = safe_page.locator("#join-dialog")
    expect(dialog).to_contain_text("公式目安")
    saved_arrival = arrival_value(safe_page)
    safe_page.get_by_label("到着予定", exact=True).fill(saved_arrival)
    safe_page.get_by_role("button", name="＋10分", exact=True).click()
    expect(safe_page.get_by_label("到着予定", exact=True)).to_have_value(
        arrival_value(safe_page, 1 + 10 / 60)
    )
    safe_page.get_by_label("到着予定", exact=True).fill(saved_arrival)
    assert dialog.evaluate("element => element.scrollWidth <= element.clientWidth")
    safe_page.screenshot(path=f"test-results/official-automation/form-{viewport[0]}.png")
    safe_page.get_by_role("button", name="自動受付を開始", exact=True).click()
    panel = safe_page.get_by_role("region", name="自動受付の予定")
    expect(panel).to_contain_text("浜松テスト店")
    safe_page.screenshot(
        path=f"test-results/official-automation/panel-{viewport[0]}.png", full_page=True
    )
    assert len(browser_service.automation_requests) == 1
    assert browser_service.automation_requests[0].timing_policy == "official"
    assert browser_service.automation_requests[0].answer1 == 1
    assert browser_service.submissions == []
    safe_page.reload()
    panel.get_by_role("button", name="設定を変更").click()
    expect(safe_page.get_by_label("到着予定", exact=True)).to_have_value(saved_arrival)
    safe_page.get_by_label("到着予定", exact=True).fill("2030-01-01T23:55")
    safe_page.get_by_role("button", name="＋10分", exact=True).click()
    safe_page.get_by_role("button", name="＋10分", exact=True).click()
    expect(safe_page.get_by_label("到着予定", exact=True)).to_have_value("2030-01-02T00:15")
    for _ in range(2):
        safe_page.get_by_role("button", name="−10分", exact=True).click()
    expect(safe_page.get_by_label("到着予定", exact=True)).to_have_value("2030-01-01T23:55")
    safe_page.get_by_label("到着予定", exact=True).fill(arrival_value(safe_page, 2))
    safe_page.get_by_role("button", name="変更を保存", exact=True).click()
    expect(dialog).not_to_be_visible()
    panel.get_by_role("button", name="監視を停止").click()
    expect(panel).to_contain_text("停止済み")
    expect(panel.get_by_role("button", name="設定を変更")).to_have_count(0)
    assert browser_service.submissions == []
    assert safe_page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_task_read_failure_preserves_saved_schedule(safe_page: Page, browser_base_url: str) -> None:
    from playwright.sync_api import expect

    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="自動受付", exact=True).click()
    safe_page.get_by_label("到着予定", exact=True).fill(arrival_value(safe_page))
    safe_page.get_by_role("button", name="自動受付を開始", exact=True).click()
    panel = safe_page.get_by_role("region", name="自動受付の予定")
    expect(panel).to_contain_text("浜松テスト店")
    safe_page.route("**/api/automation", lambda r: r.fulfill(status=200, body="not-json"))
    safe_page.get_by_role("button", name="最新情報に更新").click()
    expect(panel).to_contain_text("更新できません")
    expect(panel).to_contain_text("浜松テスト店")
