from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from .geometry import Rect, assert_inside_viewport, assert_no_overlap, box

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

pytestmark = pytest.mark.browser
VIEWPORTS = [(1440, 900), (390, 844), (320, 568)]


class StubLocator:
    def __init__(
        self,
        bounding_box: dict[str, float] | None,
        *,
        visible: bool = True,
    ) -> None:
        self._bounding_box = bounding_box
        self._visible = visible

    def bounding_box(
        self,
        *,
        timeout: float | timedelta | None = None,
    ) -> dict[str, float] | None:
        del timeout
        return self._bounding_box

    def is_visible(self) -> bool:
        return self._visible


class StubPage:
    def __init__(self, width: int, height: int) -> None:
        self.viewport_size = {"width": width, "height": height}


def test_rectangles_overlap_only_when_their_areas_intersect() -> None:
    assert Rect(0, 0, 10, 10).overlaps(Rect(9, 9, 10, 10)) is True
    assert Rect(0, 0, 10, 10).overlaps(Rect(10, 0, 10, 10)) is False
    assert Rect(0, 0, 10, 10).overlaps(Rect(0, 10, 10, 10)) is False


def test_zero_area_rectangles_do_not_overlap() -> None:
    assert Rect(5, 5, 0, 10).overlaps(Rect(0, 0, 10, 10)) is False
    assert Rect(5, 5, 10, 0).overlaps(Rect(0, 0, 10, 10)) is False


def test_box_converts_the_browser_bounding_box() -> None:
    locator = StubLocator({"x": 1.5, "y": 2.5, "width": 20.0, "height": 30.0})

    assert box(locator) == Rect(1.5, 2.5, 20.0, 30.0)


def test_assert_no_overlap_rejects_intersecting_rectangles() -> None:
    with pytest.raises(AssertionError, match="overlap"):
        assert_no_overlap(Rect(0, 0, 10, 10), Rect(9, 9, 10, 10))


def test_inside_viewport_checks_both_horizontal_edges() -> None:
    page = StubPage(320, 568)

    assert_inside_viewport(
        StubLocator({"x": 0, "y": 10, "width": 320, "height": 20}),
        page,
    )
    with pytest.raises(AssertionError, match="left edge"):
        assert_inside_viewport(
            StubLocator({"x": -1, "y": 10, "width": 20, "height": 20}),
            page,
        )
    with pytest.raises(AssertionError, match="right edge"):
        assert_inside_viewport(
            StubLocator({"x": 310, "y": 10, "width": 11, "height": 20}),
            page,
        )


@pytest.mark.parametrize(
    "bounding_box",
    [
        {"x": 10, "y": 10, "width": 0, "height": 20},
        {"x": 10, "y": 10, "width": 20, "height": 0},
    ],
)
def test_inside_viewport_rejects_visible_zero_area_elements(
    bounding_box: dict[str, float],
) -> None:
    with pytest.raises(AssertionError, match="positive area"):
        assert_inside_viewport(StubLocator(bounding_box), StubPage(320, 568))


def assert_no_horizontal_overflow(page: Page) -> None:
    dimensions = page.evaluate(
        """() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
        })"""
    )
    assert dimensions["scrollWidth"] <= dimensions["clientWidth"], dimensions


def assert_dialog_reachable(dialog: Locator, page: Page) -> None:
    assert_inside_viewport(dialog, page)
    dialog_box = box(dialog)
    viewport = page.viewport_size
    assert viewport is not None
    assert dialog_box.y >= 0, f"dialog top is outside viewport: {dialog_box!r}"
    assert dialog_box.bottom <= viewport["height"], (
        f"dialog bottom is outside viewport: {dialog_box!r}; viewport height={viewport['height']}"
    )
    dimensions = dialog.evaluate(
        """element => ({
            clientHeight: element.clientHeight,
            clientWidth: element.clientWidth,
            overflowY: getComputedStyle(element).overflowY,
            scrollHeight: element.scrollHeight,
            scrollWidth: element.scrollWidth,
        })"""
    )
    assert dimensions["scrollWidth"] <= dimensions["clientWidth"], dimensions
    assert dimensions["overflowY"] in {"auto", "scroll"}, dimensions
    assert_no_horizontal_overflow(page)


def assert_visible_buttons_inside_viewport(page: Page) -> None:
    for button in page.get_by_role("button").all():
        if button.is_visible():
            assert_inside_viewport(button, page)


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=["desktop", "mobile", "narrow"])
def test_console_geometry_across_supported_viewports(
    safe_page: Page,
    browser_base_url: str,
    viewport: tuple[int, int],
    assert_clean_browser: Callable[[], None],
) -> None:
    from playwright.sync_api import expect

    del viewport
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="すべて").click()
    rows = safe_page.locator(".shop-row")
    expect(rows).to_have_count(4)
    expect(safe_page.get_by_text("現在の順番待ちはありません", exact=True)).to_be_visible()

    header = safe_page.locator(".console-header")
    queue_band = safe_page.get_by_role("region", name="現在の順番待ち")
    toolbar = safe_page.locator(".toolbar")
    counts = safe_page.get_by_label("店舗数")
    shop_list = safe_page.get_by_role("region", name="店舗一覧").locator("#shop-list")
    first_row = rows.first
    for region in (header, queue_band, toolbar, counts, shop_list):
        assert_inside_viewport(region, safe_page)
    assert_visible_buttons_inside_viewport(safe_page)
    assert_no_horizontal_overflow(safe_page)

    assert_no_overlap(box(header), box(queue_band))
    assert_no_overlap(box(queue_band), box(toolbar))
    assert_no_overlap(box(toolbar), box(first_row))
    assert_no_overlap(box(counts), box(first_row))
    first_action = first_row.get_by_role("button")
    for content in first_row.locator(":scope > :not(.join-button)").all():
        assert_no_overlap(box(content), box(first_action))

    empty_queue_height = box(queue_band).height

    safe_page.get_by_role("button", name="設定").click()
    settings_dialog = safe_page.get_by_role("dialog", name="設定")
    expect(settings_dialog.locator("#settings-form")).to_have_attribute("aria-busy", "false")
    assert_dialog_reachable(settings_dialog, safe_page)
    settings_dialog.get_by_role("button", name="閉じる").click()
    expect(settings_dialog).to_be_hidden()

    safe_page.get_by_role("button", name="今すぐ受付").click()
    join_dialog = safe_page.get_by_role("dialog", name="浜松テスト店")
    expect(join_dialog.locator("#join-form")).to_have_attribute("aria-busy", "false")
    assert_dialog_reachable(join_dialog, safe_page)
    waiting_url = f"{browser_base_url}/api/merchants/sawayaka/waiting"
    with safe_page.expect_request_finished(
        lambda request: request.url == waiting_url and request.method == "GET",
        timeout=5_000,
    ):
        join_dialog.get_by_role("button", name="この内容で順番待ちを申し込む").click()

    expect(join_dialog).to_be_hidden()
    expect(queue_band.get_by_text("101", exact=True)).to_be_visible()
    assert box(queue_band).height == empty_queue_height
    assert_visible_buttons_inside_viewport(safe_page)
    assert_no_horizontal_overflow(safe_page)

    queue_band.get_by_role("button", name="取消").click()
    cancel_dialog = safe_page.get_by_role("dialog", name="順番待ちを取り消しますか")
    expect(cancel_dialog).to_be_visible()
    assert_dialog_reachable(cancel_dialog, safe_page)
    cancel_dialog.get_by_role("button", name="閉じる").click()
    expect(cancel_dialog).to_be_hidden()
    assert_clean_browser()
