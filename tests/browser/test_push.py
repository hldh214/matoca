import json

import pytest
from playwright.sync_api import Page, Route, expect

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("path", ["/", "/merchants/sawayaka"])
def test_notification_gesture_enable_test_disable_and_history(
    safe_page: Page,
    browser_base_url: str,
    path: str,
) -> None:
    calls = []

    def api(route: Route) -> None:
        calls.append((route.request.method, route.request.url))
        suffix = route.request.url.split("/api/push/")[1]
        payload = [] if suffix == "history" else {"public_key": "B" + "A" * 86}
        if suffix == "subscriptions":
            payload = {"id": "synthetic-browser"}
        if suffix == "history":
            payload = [
                {
                    "title": "通知のテスト",
                    "body": "確認用の履歴",
                    "created_at": "2026-09-15T03:00:00Z",
                }
            ]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    safe_page.route("**/api/push/**", api)
    safe_page.add_init_script("""(() => {
      window.permissionCalls = 0;
      Object.defineProperty(Notification, 'permission', {get: () => 'default'});
      Notification.requestPermission = async () => { window.permissionCalls++; return 'granted'; };
      let current = null;
      const manager = {getSubscription: async () => current,
        subscribe: async () => { current = {endpoint: 'https://fcm.googleapis.com/fake',
          toJSON: () => ({endpoint:'https://fcm.googleapis.com/fake',keys:{auth:'fake',p256dh:'fake'}}),
          unsubscribe: async () => {current=null; return true;}}; return current; }};
      Object.defineProperty(navigator, 'serviceWorker', {value: {
        getRegistration: async () => ({pushManager:manager}),
        register: async () => ({pushManager:manager}),
        ready: Promise.resolve({pushManager:manager})}});
    })();""")
    safe_page.goto(browser_base_url + path)
    assert safe_page.evaluate("window.permissionCalls") == 0
    if path != "/":
        safe_page.get_by_role("button", name="設定", exact=True).click()
    safe_page.get_by_role("button", name="通知の設定", exact=True).click()
    expect(safe_page.get_by_text("確認用の履歴")).to_be_visible()
    assert safe_page.evaluate("window.permissionCalls") == 0
    safe_page.get_by_role("button", name="通知を有効にする", exact=True).click()
    expect(safe_page.get_by_text("このブラウザーの通知は有効です", exact=True)).to_be_visible()
    assert safe_page.evaluate("window.permissionCalls") == 1
    safe_page.get_by_role("button", name="テスト通知を送る").click()
    expect(
        safe_page.get_by_text("テスト通知を送信待ちに追加しました。端末で受信を確認してください")
    ).to_be_visible()
    safe_page.get_by_role("button", name="通知を無効にする").click()
    expect(safe_page.get_by_text("このブラウザーの通知を無効にしました")).to_be_visible()
    assert any(method == "DELETE" for method, _ in calls)


@pytest.mark.parametrize("mode", ["insecure", "denied", "error"])
def test_notification_unavailable_or_denied_never_registers(
    safe_page: Page,
    browser_base_url: str,
    mode: str,
) -> None:
    calls = []

    def api(route: Route) -> None:
        calls.append(route.request.method)
        route.fulfill(status=200, content_type="application/json", body="[]")

    safe_page.route("**/api/push/**", api)
    if mode == "insecure":
        safe_page.add_init_script("Object.defineProperty(window, 'isSecureContext', {value:false})")
    elif mode == "denied":
        safe_page.add_init_script("""Notification.requestPermission = async () => 'denied';
          Object.defineProperty(navigator, 'serviceWorker', {value: {
            getRegistration: async () => null}});""")
    else:
        safe_page.add_init_script("""Object.defineProperty(navigator, 'serviceWorker', {value: {
            getRegistration: async () => {throw Error('ENGLISH SECRET ENDPOINT');}}});""")
    safe_page.goto(browser_base_url)
    safe_page.get_by_role("button", name="通知の設定", exact=True).click()
    if mode == "insecure":
        expect(safe_page.get_by_role("button", name="通知を有効にする")).to_be_disabled()
        expect(
            safe_page.get_by_text(
                "この環境では通知を利用できません。HTTPSで開き、対応ブラウザーを使用してください"
            )
        ).to_be_visible()
    elif mode == "denied":
        safe_page.get_by_role("button", name="通知を有効にする").click()
        expect(
            safe_page.get_by_text("通知は許可されませんでした。ブラウザーの設定を確認してください")
        ).to_be_visible()
    else:
        expect(
            safe_page.get_by_text(
                "通知を設定できませんでした。接続とブラウザーの設定を確認してください"
            )
        ).to_be_visible()
        expect(safe_page.get_by_text("ENGLISH SECRET ENDPOINT")).to_have_count(0)
    assert calls == ([] if mode == "error" else ["GET"])
