from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page

    from .fake_service import BrowserFakeService

pytestmark = pytest.mark.browser


def saved_edit_task(browser_service, mode="simulation"):
    from datetime import UTC, datetime

    from matoca_service.automation.models import AutomationTask
    from matoca_service.automation.runner import form_signature

    forms = browser_service._available_shop.forms
    forms.confirm_items[0]["sub_items"].append(
        {
            "enable": True,
            "sub_item_index": 4,
            "text": "保存済みの選択",
        }
    )
    task = AutomationTask(
        id="edit-task",
        merchant_key="sawayaka",
        mode=mode,
        shop_id=3272,
        shop_name="浜松テスト店",
        arrival_at=datetime(2030, 9, 16, 10, 30, tzinfo=UTC),
        created_at=datetime(2026, 9, 10, tzinfo=UTC),
        next_evaluation_at=datetime(2026, 9, 10, tzinfo=UTC),
        adult_count=4,
        child_count=2,
        answer1=4,
        early_tolerance_minutes=9,
        model_error_minutes=37,
        in_advance_information="保存済みの連絡",
        consent=True,
        form_signature=form_signature(browser_service._available_shop),
        version=7,
    )
    browser_service._automation_tasks.append(task)
    return task


def test_decision_history_groups_repeats_shows_recent_and_survives_refresh(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService, monkeypatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from playwright.sync_api import expect

    from matoca_service.automation.decisions import TimingDecision

    task = saved_edit_task(browser_service)
    start = datetime(2026, 9, 20, 1, tzinfo=UTC)
    rows = [
        TimingDecision(
            evaluated_at=start + timedelta(minutes=i),
            checked_at=start + timedelta(minutes=i),
            fresh=True,
            official_minutes=None if i < 3 else 20 + i,
            official_is_more=False,
            prediction=None,
            arrival_at=task.arrival_at,
            early_tolerance_minutes=15,
            model_error_minutes=15,
            reason_code="shop_closed" if i < 3 else "too_early",
            reason="営業時間外" if i < 3 else f"評価記録{i}",
        )
        for i in range(15)
    ]

    async def history(task_id):
        assert task_id == task.id
        return rows

    monkeypatch.setattr(browser_service, "automation_history", history)
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    band = safe_page.get_by_role("region", name="到着予定の自動受付")
    band.get_by_text("判断履歴を見る").click()
    entries = band.locator(".automation-history-entry")
    expect(entries).to_have_count(10)
    expect(entries.first).to_contain_text("評価記録14")
    band.get_by_role("button", name="すべての判断を表示").click()
    expect(entries).to_have_count(13)
    expect(entries.last).to_contain_text("同じ判断が3回連続")
    safe_page.get_by_role("button", name="最新情報に更新").click()
    expect(entries).to_have_count(13)
    expect(band.locator("details")).to_have_attribute("open", "")


@pytest.mark.parametrize("mode", ["live", "simulation"])
def test_edit_prefills_saved_values_reconfirms_consent_and_reopens_saved_result(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService, mode: str
) -> None:
    from playwright.sync_api import expect

    saved_edit_task(browser_service, mode)
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    band = safe_page.get_by_role("region", name="到着予定の自動受付")
    band.get_by_role("button", name="設定を編集").click()
    dialog = safe_page.locator("#join-dialog")
    expect(dialog.locator("#join-form")).to_have_attribute("aria-busy", "false")
    expect(dialog.locator("#adult-count")).to_have_text("4")
    expect(dialog.locator("#child-count")).to_have_text("2")
    expect(dialog.locator('[data-answer="1"]')).to_have_value("4")
    expect(dialog.locator("#arrival-at")).to_have_value("2030-09-16T19:30")
    expect(dialog.locator("#model-error")).to_have_value("37")
    expect(dialog.locator("#early-tolerance")).to_have_value("9")
    expect(dialog.locator("#automation-mode")).to_have_value(mode)
    expect(dialog.locator("#automation-mode")).to_be_disabled()
    expect(dialog).to_contain_text("基準37分 + 追加10分 → 47分")
    save = dialog.get_by_role("button", name="変更を保存する")
    expect(save).to_be_disabled()
    dialog.get_by_label("閉じる", exact=True).click()
    band.get_by_role("button", name="設定を編集").click()
    expect(dialog.locator("#model-error")).to_have_value("37")
    dialog.get_by_role("button", name="大人を減らす").click()
    dialog.get_by_role("button", name="提案の余裕をこの設定に適用").click()
    dialog.get_by_role("checkbox").check()
    save.click()
    expect(dialog).not_to_be_visible()
    assert browser_service._automation_tasks[0].adult_count == 3
    assert browser_service._automation_tasks[0].model_error_minutes == 47
    assert browser_service._automation_tasks[0].in_advance_information == "保存済みの連絡"
    assert browser_service._automation_tasks[0].mode == mode
    assert browser_service.submissions == []
    band.get_by_role("button", name="設定を編集").click()
    expect(dialog.locator("#adult-count")).to_have_text("3")
    expect(dialog.locator("#model-error")).to_have_value("47")
    if mode == "simulation":
        expect(dialog.locator("#simulation-note")).to_be_visible()


def test_edit_conflict_keeps_inputs_and_reload_requires_explicit_review(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
    _browser_diagnostics,
) -> None:
    from playwright.sync_api import expect

    saved_edit_task(browser_service)
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="設定を編集").click()
    dialog = safe_page.locator("#join-dialog")
    expect(dialog.locator("#join-form")).to_have_attribute("aria-busy", "false")
    dialog.get_by_role("button", name="大人を減らす").click()
    dialog.get_by_label("到着予定日時").fill("2030-09-16T20:00")
    dialog.get_by_role("checkbox").check()
    browser_service._automation_tasks[0].version += 1
    dialog.get_by_role("button", name="変更を保存する").click()
    expect(dialog.locator("#join-error")).to_contain_text("入力は保持")
    expect(dialog.locator("#adult-count")).to_have_text("3")
    expect(dialog.locator("#arrival-at")).to_have_value("2030-09-16T20:00")
    expect(dialog.get_by_role("button", name="変更を保存する")).to_be_disabled()
    dialog.get_by_role("button", name="最新情報を読み込んで確認").click()
    expect(dialog.locator("#join-error")).to_contain_text("内容を確認")
    expect(dialog.locator("#adult-count")).to_have_text("3")
    expect(dialog.locator("#arrival-at")).to_have_value("2030-09-16T20:00")
    expect(dialog.get_by_role("checkbox")).not_to_be_checked()
    dialog.get_by_role("checkbox").check()
    dialog.get_by_role("button", name="変更を保存する").click()
    expect(dialog).not_to_be_visible()
    # Chromium logs this deliberately exercised HTTP conflict as a resource error.
    expected_error = "Failed to load resource: the server responded with a status of 409 (Conflict)"
    assert _browser_diagnostics.console_errors == [expected_error]
    _browser_diagnostics.console_errors.remove(expected_error)


def test_edit_changed_meaning_requires_new_explicit_selection_even_single_option(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from playwright.sync_api import expect

    task = saved_edit_task(browser_service)
    task.state = "needs_attention"
    browser_service._available_shop.forms.confirm_items[0]["sub_items"] = [
        {"enable": True, "sub_item_index": 4, "text": "変更後の選択内容"},
    ]
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="設定を編集").click()
    dialog = safe_page.locator("#join-dialog")
    expect(dialog.locator("#join-form")).to_have_attribute("aria-busy", "false")
    choice = dialog.locator('[data-answer="1"]')
    expect(choice).to_have_value("")
    dialog.get_by_role("checkbox").check()
    expect(dialog.get_by_role("button", name="変更を保存する")).to_be_disabled()
    choice.select_option("4")
    dialog.get_by_role("button", name="変更を保存する").click()
    expect(dialog).not_to_be_visible()


def test_edit_keeps_outdated_party_counts_visible_until_user_corrects_them(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from playwright.sync_api import expect

    saved_edit_task(browser_service)
    browser_service._available_shop.forms.max_adult = 2
    browser_service._available_shop.forms.min_child = 0
    browser_service._available_shop.forms.max_child = 0
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="設定を編集").click()
    dialog = safe_page.locator("#join-dialog")
    expect(dialog.locator("#join-form")).to_have_attribute("aria-busy", "false")
    expect(dialog.locator("#adult-count")).to_have_text("4")
    expect(dialog.locator("#child-count")).to_be_visible()
    expect(dialog.locator("#child-count")).to_have_text("2")
    dialog.get_by_role("checkbox").check()
    expect(dialog.get_by_role("button", name="変更を保存する")).to_be_disabled()
    expect(dialog.get_by_role("button", name="大人を減らす")).to_be_enabled()
    dialog.get_by_role("button", name="大人を減らす").click()
    dialog.get_by_role("button", name="子どもを減らす").click()
    expect(dialog.locator("#adult-count")).to_have_text("2")
    expect(dialog.locator("#child-count")).to_have_text("0")
    expect(dialog.get_by_role("button", name="変更を保存する")).to_be_enabled()


def test_simulation_consent_label_and_decision_history(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from playwright.sync_api import expect

    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="自動受付を設定").click()
    dialog = safe_page.locator("#join-dialog")
    dialog.get_by_label("実行方法").select_option("simulation")
    expect(
        dialog.get_by_text(
            "1分ごとに評価し、条件が整えば追加の確認なしで申し込みます。", exact=False
        )
    ).not_to_be_visible()
    expect(
        dialog.get_by_text("1分ごとに受付条件を確認して記録します。", exact=False)
    ).to_be_visible()
    dialog.get_by_label("到着予定日時").fill("2030-09-16T19:30")
    activate = dialog.get_by_role("button", name="シミュレーションを開始する")
    expect(activate).to_be_disabled()
    dialog.get_by_label(
        "実際の申込を行わず、受付条件の確認と記録を開始することに同意します"
    ).check()
    activate.click()
    band = safe_page.get_by_role("region", name="到着予定の自動受付")
    expect(band).to_contain_text("シミュレーション")
    band.get_by_text("判断履歴を見る").click()
    expect(band).to_contain_text("評価記録はまだありません")
    assert browser_service.automation_requests[0].mode == "simulation"
    assert browser_service.submissions == []


def test_replay_displays_timing_only_result_without_creating_task(
    safe_page: Page, browser_base_url: str, browser_service: BrowserFakeService
) -> None:
    from playwright.sync_api import expect

    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="履歴を見る").first.click()
    dialog = safe_page.locator("#history-dialog")
    expect(dialog).to_contain_text("公式目安の変動（実測待ち時間とは別）")  # noqa: RUF001
    expect(dialog).to_contain_text("店舗・同じ曜日区分と時間帯")
    dialog.get_by_label("再現する到着時刻", exact=False).fill("18:00")
    dialog.get_by_role("button", name="受付時刻を再現する").click()
    expect(dialog).to_contain_text("当時のフォームとアカウントの受付状況は不明")
    expect(dialog).to_contain_text("記録の範囲では時刻条件の成立は確認できません")
    expect(dialog).to_contain_text("固定余裕と提案余裕の比較")
    expect(dialog).to_contain_text("基準15分 + 追加10分 → 25分")
    assert browser_service.automation_requests == []
    assert browser_service.submissions == []


@pytest.mark.parametrize("baseline,expected", [(15, 25), (115, 120)])
def test_trend_margin_requires_click_and_only_changes_new_task(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
    baseline: int,
    expected: int,
) -> None:
    from playwright.sync_api import expect

    saved_preferences = browser_service.preferences.model_dump()
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="自動受付を設定").click()
    dialog = safe_page.locator("#join-dialog")
    dialog.get_by_text("予測の詳細設定", exact=True).click()
    margin = dialog.locator("#model-error")
    margin.fill(str(baseline))
    expect(dialog).to_contain_text("下方修正の90%点")
    expect(margin).to_have_value(str(baseline))
    assert browser_service.automation_requests == []
    apply = dialog.get_by_role("button", name="提案の余裕をこの設定に適用")
    apply.click()
    expect(margin).to_have_value(str(expected))
    expect(apply).to_be_disabled()
    dialog.get_by_label("実行方法").select_option("simulation")
    dialog.get_by_label("到着予定日時").fill("2030-09-16T19:30")
    dialog.get_by_role("checkbox").check()
    dialog.get_by_role("button", name="シミュレーションを開始する").click()
    expect(dialog).not_to_be_visible()
    assert browser_service.automation_requests[0].model_error_minutes == expected
    assert browser_service.preferences.model_dump() == saved_preferences
    assert browser_service.submissions == []


def test_sparse_trend_is_explicit_and_cannot_be_applied(
    safe_page: Page,
    browser_base_url: str,
    browser_service: BrowserFakeService,
) -> None:
    from playwright.sync_api import expect

    browser_service.trend_sample_count = 19
    safe_page.goto(f"{browser_base_url}/merchants/sawayaka")
    safe_page.get_by_role("button", name="自動受付を設定").click()
    dialog = safe_page.locator("#join-dialog")
    expect(dialog).to_contain_text("19区間 / 必要20区間")
    expect(dialog.get_by_role("button", name="提案の余裕をこの設定に適用")).to_be_disabled()
    expect(dialog.locator("#model-error")).to_have_value("15")


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
