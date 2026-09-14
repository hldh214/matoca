function time(value) {
  return new Date(value).toLocaleTimeString("ja-JP", {hour: "2-digit", minute: "2-digit"});
}

function statusText(item) {
  return {called: "呼び出し済み", cancelled: "取消済み", unknown: "結果を確認できません"}[item.status] || "受付中";
}

function render(queue) {
  const target = document.querySelector("#personal-queues");
  target.replaceChildren();
  const active = queue.filter((item) => item.status === "active");
  const shown = active.length ? active : queue.slice(0, 1);
  if (!shown.length) {
    target.textContent = "現在の順番待ちはありません";
    return;
  }
  for (const item of shown) {
    const latest = item.observations.at(-1);
    const link = document.createElement("a");
    link.className = "personal-queue-row";
    link.href = `/merchants/${encodeURIComponent(item.merchant_key)}`;
    const name = document.createElement("strong");
    name.textContent = `${item.merchant_name || item.merchant_key}・${item.shop_name || "受付中"}`;
    const status = document.createElement("span");
    status.textContent = `${statusText(item)}・受付番号 ${item.number ?? "—"}・前 ${latest?.count ?? "—"}組・更新 ${latest ? time(latest.observed_at) : "—"}${item.stale ? "・更新待ち" : ""}`;
    link.append(name, status);
    target.append(link);
  }
}

function refresh() {
  if (document.hidden) return;
  fetch("/api/queues", {credentials: "same-origin"})
    .then((response) => response.ok ? response.json() : Promise.reject())
    .then(render)
    .catch(() => { document.querySelector("#personal-queues").textContent = "順番待ちを確認できませんでした"; });
}

document.addEventListener("visibilitychange", refresh);
setInterval(refresh, 30_000);
refresh();
