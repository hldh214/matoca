from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page

    from .fake_service import BrowserFakeService

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("viewport", [(390, 844), (1440, 900)])
def test_automatic_arrival_consent_and_cancel_monitoring(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
    viewport: tuple[int, int],
) -> None:
    from playwright.sync_api import expect

    del viewport

    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="自動受付を設定").click()
    dialog = safe_page.locator("#join-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.locator("#join-form")).to_have_attribute("aria-busy", "false")
    expect(dialog.locator("#adult-count")).to_have_text("2")
    expect(dialog.locator("#child-count")).to_have_text("1")
    expect(dialog.get_by_text("到着予定の2分後まで", exact=False)).to_be_visible()
    activate = dialog.get_by_role("button", name="自動受付を有効にする")
    expect(activate).to_be_disabled()
    dialog.get_by_label("到着予定日時").fill("2030-09-16T19:30")
    dialog.get_by_label(
        "条件が整ったら、追加の確認なしで順番待ちを申し込むことに同意します"
    ).check()
    expect(activate).to_be_enabled()
    activate.click()
    expect(dialog).not_to_be_visible()
    band = safe_page.get_by_role("region", name="到着予定の自動受付")
    expect(band).to_contain_text("浜松テスト店")
    expect(band).to_contain_text("次回評価")
    assert browser_service.submissions == []
    assert browser_service.automation_requests[0].answer1 == 1
    assert browser_service.automation_requests[0].child_count == 1
    assert browser_service.automation_requests[0].early_tolerance_minutes == 15
    safe_page.reload()
    band.get_by_role("button", name="監視を取り消す").click()
    expect(band).to_contain_text("監視を取り消しました")
    assert browser_service.submissions == []


def test_unknown_result_resolution_requires_explicit_confirmation(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from datetime import UTC, datetime

    from playwright.sync_api import expect

    from matoca_service.automation.models import AutomationTask

    browser_service._automation_tasks.append(
        AutomationTask(
            id="uncertain",
            merchant_key="sawayaka",
            shop_id=3272,
            shop_name="浜松テスト店",
            arrival_at=datetime(2030, 1, 1, tzinfo=UTC),
            created_at=datetime(2030, 1, 1, tzinfo=UTC),
            consent=True,
            form_signature="synthetic",
            intent_id="intent-unknown",
            state="needs_attention",
            last_decision="受付結果が不明です。再送しません。",
        )
    )
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    band = safe_page.get_by_role("region", name="到着予定の自動受付")
    resolve = band.get_by_role("button", name="受付がないことを確認して監視を終了")
    expect(resolve).to_be_disabled()
    band.get_by_role("checkbox").check()
    resolve.click()
    expect(band).to_contain_text("監視を取り消しました")
    assert browser_service.submissions == []


def test_manual_unknown_result_can_be_resolved_without_another_submission(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from datetime import UTC, datetime

    from playwright.sync_api import expect

    from matoca_service.tracking.models import QueueIntentSummary

    browser_service.queue_intents.append(
        QueueIntentSummary(
            intent_id="manual-unknown",
            merchant_key="sawayaka",
            shop_id=3272,
            submitted_at=datetime(2030, 1, 1, tzinfo=UTC),
            official_minutes_at_submission=30,
            official_is_more_at_submission=False,
            adult_count=2,
            child_count=0,
            source="manual",
            status="unresolved",
        )
    )
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="受付結果を確認して終了").click()
    dialog = safe_page.locator("#cancel-dialog")
    expect(dialog).to_be_visible()
    submit = dialog.get_by_role("button", name="受付がないことを確認して終了")
    expect(submit).to_be_disabled()
    dialog.get_by_role("checkbox").check()
    submit.click()
    expect(dialog).not_to_be_visible()
    expect(safe_page.get_by_text("現在の順番待ちはありません", exact=True)).to_be_visible()
    assert browser_service.submissions == []
