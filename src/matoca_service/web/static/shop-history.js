const SVG_NS = "http://www.w3.org/2000/svg";
function element(document, name, text) {
  const node = document.createElement(name);
  if (text !== undefined) node.textContent = text;
  return node;
}

function plot(document, observations, field, label, day, zone) {
  const section = element(document, "section");
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 640 200");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  const valid = observations.filter((item) => !item.error_code && Number.isFinite(item[field]) && item[field] >= 0);
  const maximum = Math.max(1, ...valid.map((item) => item[field]));
  const start = new Date(`${day}T00:00:00+09:00`).getTime();
  const format = (value) => new Date(value).toLocaleTimeString("ja-JP", {
    hour: "2-digit", minute: "2-digit", timeZone: zone});
  const node = (name, attributes, text) => {
    const value = document.createElementNS(SVG_NS, name);
    for (const [key, attribute] of Object.entries(attributes)) value.setAttribute(key, attribute);
    if (text !== undefined) value.textContent = text;
    return value;
  };
  for (const fraction of [0, 0.5, 1]) {
    const y = 155 - fraction * 125;
    svg.append(node("line", {x1: 44, x2: 610, y1: y, y2: y, class: "history-grid"}),
      node("text", {x: 38, y: y + 4, "text-anchor": "end", class: "history-axis"}, String(Math.round(maximum * fraction))));
  }
  for (const hour of [0, 6, 12, 18, 24]) {
    svg.append(node("text", {x: 44 + hour / 24 * 566, y: 184, "text-anchor": "middle", class: "history-axis"},
      format(start + hour * 3600000)));
  }
  let segment = [], previous = null;
  const flush = () => {
    if (!segment.length) return;
    svg.append(node("path", {class: "history-line", d: segment.map((point, i) => `${i ? "L" : "M"}${point}`).join(" ")}));
    if (segment.length === 1) {
      const [cx, cy] = segment[0].split(",");
      svg.append(node("circle", {cx, cy, r: 3, class: "history-point"}));
    }
    segment = [];
  };
  for (const item of observations) {
    const at = new Date(item.observed_at).getTime();
    if (item.error_code || !Number.isFinite(item[field]) || item[field] < 0 || !Number.isFinite(at)) {
      flush(); previous = null; continue;
    }
    if (previous !== null && at - previous > 2 * 60000) flush();
    const x = 44 + (at - start) / 86400000 * 566;
    const y = 155 - item[field] / maximum * 125;
    segment.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    const lower = field === "official_waiting_minutes" && item.official_waiting_is_more;
    const marker = node("circle", {cx: x, cy: y, r: lower ? 4 : 3,
      class: lower ? "history-lower-bound" : "history-hover"});
    marker.append(node("title", {}, `${format(at)}：${item[field]}${field === "current_waiting" ? "組" : "分"}${lower ? "以上" : ""}`));
    svg.append(marker);
    previous = at;
  }
  flush();
  section.append(element(document, "h3", label), svg);
  if (!valid.length) section.append(element(document, "p", "有効な記録がありません"));
  return section;
}

export class ShopHistoryDialog {
  constructor(document, api) {
    Object.assign(this, {document, api});
    this.dialog = document.querySelector("#history-dialog");
    this.day = document.querySelector("#history-day");
    this.body = document.querySelector("#history-body");
    this.revision = 0;
    this.day.addEventListener("change", () => this.load());
    this.dialog.addEventListener("close", () => { this.revision++; });
    document.querySelector("#history-refresh").addEventListener("click", () => this.load());
  }

  open(shop) {
    this.shop = shop;
    this.document.querySelector("#history-title").textContent = `${shop.sub_name || shop.name}の履歴`;
    const parts = new Intl.DateTimeFormat("en-CA", {timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit"}).formatToParts(new Date());
    const value = (type) => parts.find((part) => part.type === type).value;
    this.day.value = `${value("year")}-${value("month")}-${value("day")}`;
    this.dialog.showModal();
    return this.load();
  }

  async load() {
    const revision = ++this.revision;
    if (!this.day.value) {
      this.body.replaceChildren(element(this.document, "p", "日付を選択してください"));
      return;
    }
    this.body.replaceChildren(element(this.document, "p", "履歴を読み込んでいます"));
    try {
      const data = await this.api.shopHistory(this.shop.id, this.day.value);
      if (revision !== this.revision || !this.dialog.open) return;
      const summary = element(this.document, "p", `表示時刻：${this.api.timezone}・${data.observations.length}件の記録`);
      if (!data.observations.length) {
        this.body.replaceChildren(summary, element(this.document, "p", "この日の記録はありません"));
        return;
      }
      this.body.replaceChildren(summary,
        plot(this.document, data.observations, "current_waiting", "待ち組数（組）", data.day, this.api.timezone),
        plot(this.document, data.observations, "official_waiting_minutes", "公式待ち時間（分）", data.day, this.api.timezone),
        element(this.document, "p", "取得できなかった区間や2分を超える間隔は線をつなぎません。丸印は「以上」の目安です。実際の呼出時間ではありません。"));
    } catch (error) {
      if (revision !== this.revision || !this.dialog.open) return;
      this.body.replaceChildren(element(this.document, "p", error.detail || "履歴を取得できませんでした。「更新」で再試行してください"));
    }
  }
}
