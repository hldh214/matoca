function time(value) {
  return new Date(value).toLocaleTimeString("ja-JP", {hour: "2-digit", minute: "2-digit"});
}

function statusText(item) {
  return {pending: "受付結果を確認中", unresolved: "受付結果を確認できません", called: "呼び出し済み", cancelled: "取消済み", unknown: "結果を確認できません"}[item.status] || "受付中";
}

function render(queue) {
  const target = document.querySelector("#personal-queues");
  target.replaceChildren();
  const active = queue.filter((item) => ["active", "pending", "unresolved"].includes(item.status));
  const shown = active.length ? active : queue.slice(0, 1);
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
    if (item.trajectory_minutes !== null && item.trajectory_minutes !== undefined) {
      status.textContent += `・組数推移 約${item.trajectory_minutes}分`;
    }
    if (item.prediction) {
      const confidence = {low: "低", medium: "中", high: "高"}[item.prediction.confidence];
      status.textContent += `・残り予測 ${item.prediction.fast_minutes}〜${item.prediction.typical_minutes}分`;
      status.textContent += `・信頼度 ${confidence}・実効${Number(item.prediction.effective_samples).toFixed(1)}件`;
    }
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
