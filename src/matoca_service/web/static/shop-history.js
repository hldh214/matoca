import {trendSection} from "./trends.js";

const SVG_NS = "http://www.w3.org/2000/svg";
function element(document, name, text) {
  const node = document.createElement(name);
  if (text !== undefined) node.textContent = text;
  return node;
}
function plot(document, observations, field, label, day, displayZone) {
  const section = element(document, "section");
  const heading = element(document, "h3", label);
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 640 180");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  const read = (item) => field.startsWith("prediction.")
    ? item.prediction?.[field.split(".")[1]] ?? null : item[field];
  const valid = observations.filter((item) => item.error_code === null && read(item) !== null);
  const maximum = Math.max(1, ...valid.map(read));
  const dayStart = new Date(`${day}T00:00:00+09:00`);
  let segment = [];
  let previousTime = null;
  const flush = () => {
    if (!segment.length) return;
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("class", "history-line");
    path.setAttribute("d", segment.map((point, index) => `${index ? "L" : "M"}${point}`).join(" "));
    svg.append(path);
    if (segment.length === 1) {
      const [x, y] = segment[0].split(",");
      const point = document.createElementNS(SVG_NS, "circle");
      point.setAttribute("cx", x); point.setAttribute("cy", y); point.setAttribute("r", "5");
      point.setAttribute("class", "history-point"); svg.append(point);
    }
    segment = [];
  };
  observations.forEach((item, index) => {
    const fieldValue = read(item);
    if (item.error_code !== null || fieldValue === null) return flush();
    const itemTime = new Date(item.observed_at);
    if (previousTime !== null && itemTime - previousTime > 20 * 60 * 1000) flush();
    const x = 42 + (itemTime - dayStart) * 570 / (24 * 60 * 60 * 1000);
    const y = 145 - fieldValue * 115 / maximum;
    segment.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    if (field === "official_waiting_minutes" && item.official_waiting_is_more) {
      const marker = document.createElementNS(SVG_NS, "circle");
      marker.setAttribute("cx", x.toFixed(1)); marker.setAttribute("cy", y.toFixed(1));
      marker.setAttribute("r", "7"); marker.setAttribute("class", "history-lower-bound");
      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = `${item[field]}分以上`; marker.append(title); svg.append(marker);
    }
    previousTime = itemTime;
  });
  flush();
  for (const [text, y] of [[String(maximum), 30], [String(Math.round(maximum / 2)), 88], ["0", 145]]) {
    const tick = document.createElementNS(SVG_NS, "text");
    tick.setAttribute("x", "4"); tick.setAttribute("y", String(y)); tick.textContent = text;
    tick.setAttribute("class", "history-axis-label"); svg.append(tick);
  }
  const ticks = element(document, "p", observations.filter((_, index) =>
    index % Math.max(1, Math.ceil(observations.length / 4)) === 0)
    .map((item) => new Date(item.observed_at).toLocaleTimeString("ja-JP",
      {hour: "2-digit", minute: "2-digit", timeZone: displayZone})).join("　"));
  ticks.className = "history-ticks";
  section.append(heading, svg, ticks);
  return section;
}
export class ShopHistoryDialog {
  constructor(document, api) {
    this.document = document;
    this.api = api;
    this.dialog = document.querySelector("#history-dialog");
    this.day = document.querySelector("#history-day");
    this.body = document.querySelector("#history-body");
    this.revision = 0;
    try { this.displayZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Tokyo"; }
    catch { this.displayZone = "Asia/Tokyo"; }
    this.day.addEventListener("change", () => this.load());
    this.replayRevision = 0;
    const section = element(document, "section");
    section.className = "replay-panel";
    section.append(element(document, "h3", "過去の記録で受付時刻を再現"), element(document, "p", "日本時間の選択日について時刻条件だけを確認します。当時のフォーム・アカウント状況は不明です。実際の申込は行いません。"));
    this.replayArrival = element(document, "input");
    this.replayArrival.type = "time";
    this.replayArrival.value = "18:00";
    const arrivalLabel = element(document, "label", "再現する到着時刻（日本時間）");
    arrivalLabel.append(this.replayArrival);
    this.replayEarly = element(document, "input");
    this.replayError = element(document, "input");
    section.append(arrivalLabel);
    for (const [input, title] of [[this.replayEarly, "再現の早着許容（分）"], [this.replayError, "再現の予測誤差（分）"]]) {
      input.type = "number"; input.min = "0"; input.max = "120"; input.value = "15";
      const label = element(document, "label", title); label.append(input); section.append(label);
    }
    const button = element(document, "button", "受付時刻を再現する");
    button.type = "button";
    this.replayOutput = element(document, "div");
    this.replayOutput.setAttribute("aria-live", "polite");
    button.addEventListener("click", () => this.runReplay());
    section.append(button, this.replayOutput);
    this.body.after(section);
  }
  open(shop, day) {
    this.shop = shop;
    this.document.querySelector("#history-title").textContent = `${shop.sub_name || shop.name}の履歴`;
    this.day.value = day;
    this.dialog.showModal();
    return this.load();
  }
  async load() {
    ++this.replayRevision;
    this.replayOutput.replaceChildren();
    const revision = ++this.revision;
    this.body.replaceChildren(element(this.document, "p", "履歴を読み込んでいます"));
    try {
      const data = await this.api.shopHistory(this.shop.id, this.day.value);
      if (revision !== this.revision) return;
      const identity = element(this.document, "div");
      identity.className = "history-identity";
      if (data.shop.address) identity.append(element(this.document, "p", data.shop.address));
      if (data.shop.tel) {
        const tel = element(this.document, "a", data.shop.tel);
        tel.href = `tel:${data.shop.tel}`; identity.append(tel);
      }
      if (data.shop.lat && data.shop.lng) {
        const map = element(this.document, "a", "地図を開く");
        map.href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${data.shop.lat},${data.shop.lng}`)}`;
        map.target = "_blank"; map.rel = "noopener noreferrer"; identity.append(map);
      }
      identity.append(element(this.document, "span", `表示時刻: ${this.displayZone}`));
      identity.append(trendSection(this.document, data.trend));
      if (!data.observations.length) {
        this.body.replaceChildren(identity, element(this.document, "p", "この日の記録はありません"));
        return;
      }
      this.body.replaceChildren(identity,
        plot(this.document, data.observations, "current_waiting", "待ち組数（組）",
          data.day, this.displayZone),
        plot(this.document, data.observations, "official_waiting_minutes", "公式待ち時間（分）",
          data.day, this.displayZone),
        ...(data.observations.some((item) => item.prediction)
          ? [plot(this.document, data.observations, "prediction.typical_minutes", "通常予測（分）",
            data.day, this.displayZone)] : []),
        ...(data.observations.some((item) => item.official_waiting_is_more)
          ? [element(this.document, "p", "以上を示す点があります")] : []));
    } catch (error) {
      if (revision !== this.revision) return;
      this.body.replaceChildren(element(this.document, "p", error.detail || "履歴を取得できませんでした"));
    }
  }

  async runReplay() {
    const revision = ++this.replayRevision;
    const arrival = `${this.day.value}T${this.replayArrival.value}:00+09:00`;
    this.replayOutput.textContent = "再現しています";
    try {
      const result = await this.api.replay(this.shop.id, this.day.value, arrival, this.replayEarly.value, this.replayError.value);
      if (revision !== this.replayRevision) return;
      this.replayOutput.replaceChildren(element(this.document, "p", result.limitations),
        element(this.document, "p", result.first_would_submit_at
          ? `最初に時刻条件が成立: ${new Date(result.first_would_submit_at).toLocaleString("ja-JP", {timeZone: "Asia/Tokyo"})}（日本時間・受付成功を保証しません）`
          : "記録の範囲では時刻条件の成立は確認できません"));
      for (const row of result.decisions) this.replayOutput.append(element(this.document, "p",
        `${new Date(row.evaluated_at).toLocaleTimeString("ja-JP", {timeZone: "Asia/Tokyo"})}・${row.reason}・公式 ${row.official_minutes ?? "不明"}${row.official_is_more ? "分以上" : row.official_minutes === null ? "" : "分"}`));
      this.replayOutput.append(element(this.document, "h3", "固定余裕と提案余裕の比較（時刻条件のみ）"));
      this.replayOutput.append(element(this.document, "p", result.first_suggested_would_submit_at
        ? `提案余裕で最初に成立: ${new Date(result.first_suggested_would_submit_at).toLocaleString("ja-JP", {timeZone: "Asia/Tokyo"})}（日本時間）`
        : "提案余裕では成立なし、または資料不足です。実測の呼び出し精度は評価しません。"));
      for (const row of result.comparison || []) this.replayOutput.append(element(this.document, "p",
        `${new Date(row.evaluated_at).toLocaleTimeString("ja-JP", {timeZone: "Asia/Tokyo"})}・固定余裕${row.baseline_margin_minutes}分: ${row.fixed_would_submit ? "成立" : "未成立"}・${row.suggested_margin_minutes === null
          ? `提案は資料不足（${row.trend.sample_count}区間）`
          : `基準${row.baseline_margin_minutes}分 + 追加${row.suggested_addition_minutes}分 → ${row.suggested_margin_minutes}分（上限120分）: ${row.suggested_would_submit ? "成立" : "未成立"}`}`));
    } catch (error) {
      if (revision === this.replayRevision) this.replayOutput.textContent = error.detail || "再現できませんでした";
    }
  }
}
