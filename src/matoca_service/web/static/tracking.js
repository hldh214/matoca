import {callTime} from "./queue-time.js";

function time(value) {
  return new Date(value).toLocaleTimeString("ja-JP", {hour: "2-digit", minute: "2-digit"});
}

function statusText(item) {
  const official = {2: "順番待ち", 8: "まもなく呼び出し", 4: "呼出中", 5: "保留中", 6: "完了"}[item.observations?.at(-1)?.raw_status];
  if (official && ["active", "called"].includes(item.status)) return official;
  return {pending: "受付結果を確認中", unresolved: "受付結果を確認できません", called: "呼び出し済み", cancelled: "取消済み", unknown: "結果を確認できません"}[item.status] || "受付中";
}

function render(queue) {
  const target = document.querySelector("#personal-queues");
  target.replaceChildren();
  const active = queue.filter((item) => ["active", "called", "pending", "unresolved"].includes(item.status));
  const shown = active;
  if (!shown.length) {
    target.textContent = "現在の順番待ちはありません";
    return;
  }
  for (const item of shown) {
    const latest = item.observations?.at(-1);
    const updatedAt = latest?.observed_at ?? item.submitted_at;
    const link = document.createElement("a");
    link.className = "personal-queue-row";
    link.href = `/merchants/${encodeURIComponent(item.merchant_key)}`;
    const name = document.createElement("strong");
    name.textContent = `${item.merchant_name || item.merchant_key}・${item.shop_name || "受付中"}`;
    const status = document.createElement("span");
    status.textContent = `${statusText(item)}・受付番号 ${item.number ?? "—"}・前 ${latest?.count ?? "—"}組・更新 ${updatedAt ? time(updatedAt) : "—"}${item.stale ? "・更新待ち" : ""}`;
    const call = document.createElement("strong");
    call.className = "personal-call-time";
    call.textContent = `呼出目安（公式） ${callTime(item)}`;
    link.append(name, call, status);
    target.append(link);
  }
}

let lastQueue = null;
let busy = false;
async function refresh() {
  if (document.hidden || busy) return;
  const button = document.querySelector("#queue-refresh");
  const error = document.querySelector("#personal-queue-error");
  busy = true;
  button.disabled = true;
  try {
    const response = await fetch("/api/queues", {credentials: "same-origin"});
    if (!response.ok) throw new Error();
    const items = await response.json();
    if (!Array.isArray(items)) throw new Error();
    lastQueue = items;
    render(items);
    error.textContent = "";
  } catch {
    if (lastQueue !== null) render(lastQueue.map((item) => ({...item, stale: true})));
    else document.querySelector("#personal-queues").textContent = "順番待ちを確認できませんでした";
    error.textContent = "順番待ちを更新できませんでした。前回の情報を表示しています";
  } finally {
    busy = false;
    button.disabled = false;
  }
}

document.querySelector("#queue-refresh").addEventListener("click", refresh);
document.addEventListener("visibilitychange", refresh);
setInterval(refresh, 30_000);
refresh();
