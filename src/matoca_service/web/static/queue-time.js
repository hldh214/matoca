export function clientTimezone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Tokyo"; }
  catch { return "Asia/Tokyo"; }
}

export function callTime(item, {now = new Date(), timezone = clientTimezone()} = {}) {
  const latest = item.observations?.at(-1);
  const observed = Date.parse(latest?.observed_at);
  if (item.stale || !Number.isFinite(observed) || now - observed >= 240000) return "更新待ち";
  const state = {8: "まもなく呼び出し", 4: "呼出中", 5: "保留中", 6: "完了"}[latest.raw_status];
  if (state) return state;
  if (item.status === "called") return "呼出中";
  if (item.status !== "active") return "確認中";
  const minutes = latest.official_minutes;
  if (!Number.isFinite(minutes) || minutes < 0) return "目安なし";
  const target = new Date(observed + minutes * 60000);
  if (target <= now) return "公式目安を経過";
  try { new Intl.DateTimeFormat("ja-JP", {timeZone: timezone}).format(now); }
  catch { timezone = "Asia/Tokyo"; }
  const date = (value) => value.toLocaleDateString("ja-JP", {timeZone: timezone});
  const options = {timeZone: timezone, hour: "2-digit", minute: "2-digit", hourCycle: "h23"};
  if (date(target) !== date(now)) Object.assign(options, {month: "numeric", day: "numeric"});
  return target.toLocaleString("ja-JP", options) + (latest.official_is_more ? "以降" : "ごろ");
}
