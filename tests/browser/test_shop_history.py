# Japanese UI labels intentionally use full-width punctuation.
# ruff: noqa: RUF001
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("viewport", [(1440, 900), (390, 844), (320, 568)])
def test_history_date_charts_gaps_and_empty_day(
    safe_page: Page, browser_base_url: str, viewport: tuple[int, int]
) -> None:
    from playwright.sync_api import expect

    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="履歴", exact=True).click()
    day = safe_page.get_by_label("日付（日本時間）")
    day.fill("2026-09-10")
    dialog = safe_page.locator("#history-dialog")
    expect(dialog.get_by_role("img", name="待ち組数（組）")).to_be_visible()
    expect(dialog.get_by_role("img", name="公式待ち時間（分）")).to_be_visible()
    expect(dialog.locator("svg")).to_have_count(2)
    expect(
        dialog.get_by_role("img", name="公式待ち時間（分）").locator(".history-line")
    ).to_have_count(3)
    assert "L" in dialog.locator(".history-line").first.get_attribute("d")
    expect(dialog.locator(".history-lower-bound")).to_have_count(1)
    expect(dialog.locator("title", has_text="-1分")).to_have_count(0)
    expect(dialog).not_to_contain_text("通常予測")
    assert dialog.evaluate("element => element.scrollWidth <= element.clientWidth")
    safe_page.screenshot(path=f"test-results/history-{viewport[0]}.png")
    day.fill("2026-09-11")
    expect(dialog).to_contain_text("この日の記録はありません")
    expect(dialog.locator("svg")).to_have_count(0)
