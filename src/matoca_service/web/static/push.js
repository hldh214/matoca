const dialog = document.querySelector("#push-dialog");
const status = document.querySelector("#push-status");
const enable = document.querySelector("#push-enable");
const test = document.querySelector("#push-test");
const disable = document.querySelector("#push-disable");
let current = null;
let identity = null;
let busy = false;

function supported() {
  return window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}
function render() {
  enable.disabled = busy || !supported();
  test.disabled = busy || !identity;
  disable.disabled = busy || !current;
}
async function api(path, method = "GET", body) {
  const response = await fetch(`/api/push/${path}`, {
    method, headers: {"Content-Type": "application/json"},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error("通知の設定を保存できませんでした。接続を確認して再試行してください");
  return response.status === 204 ? null : response.json();
}
async function history() {
  const items = await api("history");
  const list = document.querySelector("#push-history");
  list.replaceChildren();
  if (!items.length) { list.textContent = "通知の履歴はまだありません"; return; }
  for (const item of items) {
    const li = document.createElement("li");
    const title = document.createElement("strong"); title.textContent = item.title;
    const body = document.createElement("p"); body.textContent = item.body;
    const time = document.createElement("time");
    time.textContent = new Date(item.created_at).toLocaleString("ja-JP");
    li.append(title, body, time); list.append(li);
  }
}
async function operation(action) {
  if (busy) return;
  busy = true; render();
  try { await action(); } catch {
    status.textContent = "通知を設定できませんでした。接続とブラウザーの設定を確認してください";
  } finally { busy = false; render(); }
}
document.querySelectorAll("[data-push-open]").forEach(button => button.addEventListener("click", () => {
  dialog.showModal();
  operation(async () => {
    if (!supported()) {
      status.textContent = "この環境では通知を利用できません。HTTPSで開き、対応ブラウザーを使用してください";
    } else {
      const registration = await navigator.serviceWorker.getRegistration("/");
      current = await registration?.pushManager.getSubscription() || null;
      identity = null;
      if (current) {
        const result = await api("subscriptions", "POST", current.toJSON()); identity = result.id;
      }
      status.textContent = current ? "このブラウザーの通知は有効です" : "このブラウザーの通知は無効です";
    }
    await history();
  });
}));
document.querySelector("#push-close").addEventListener("click", () => dialog.close());
enable.addEventListener("click", () => {
  if (busy || !supported()) return;
  // Keep permission directly in the click gesture, before any network await.
  const permission = Notification.requestPermission();
  operation(async () => {
    if (await permission !== "granted") {
      status.textContent = "通知は許可されませんでした。ブラウザーの設定を確認してください"; return;
    }
    const {public_key: publicKey} = await api("public-key", "POST");
    await navigator.serviceWorker.register("/sw.js", {scope: "/"});
    const registration = await navigator.serviceWorker.ready;
    current = await registration.pushManager.getSubscription();
    if (!current) {
      const raw = atob(publicKey.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - publicKey.length % 4) % 4));
      current = await registration.pushManager.subscribe({userVisibleOnly: true,
        applicationServerKey: Uint8Array.from(raw, c => c.charCodeAt(0))});
    }
    identity = (await api("subscriptions", "POST", current.toJSON())).id;
    status.textContent = "このブラウザーの通知は有効です";
  });
});
test.addEventListener("click", () => operation(async () => {
  await api("test", "POST", {subscription_id: identity});
  status.textContent = "テスト通知を送信待ちに追加しました。端末で受信を確認してください";
  await history();
}));
disable.addEventListener("click", () => operation(async () => {
  if (!current) return;
  await api("subscriptions", "DELETE", {endpoint: current.endpoint});
  await current.unsubscribe(); current = null; identity = null;
  status.textContent = "このブラウザーの通知を無効にしました";
}));
render();
