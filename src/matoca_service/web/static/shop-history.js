const SVG_NS = "http://www.w3.org/2000/svg";
function element(document, name, text) {
  const node = document.createElement(name);
  if (text !== undefined) node.textContent = text;
  return node;
}
function plot(document, observations, field, label) {
  const section = element(document, "section");
  const heading = element(document, "h3", label);
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 640 180");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  const valid = observations.filter((item) => item.error_code === null && item[field] !== null);
  const maximum = Math.max(1, ...valid.map((item) => item[field]));
  const dayStart = new Date(observations[0].observed_at);
  dayStart.setHours(0, 0, 0, 0);
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
    if (item.error_code !== null || item[field] === null) return flush();
    const itemTime = new Date(item.observed_at);
    if (previousTime !== null && itemTime - previousTime > 20 * 60 * 1000) flush();
    const x = 42 + (itemTime - dayStart) * 570 / (24 * 60 * 60 * 1000);
    const y = 145 - item[field] * 115 / maximum;
    segment.push(`${x.toFixed(1)},${y.toFixed(1)}`);
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
      {hour: "2-digit", minute: "2-digit"})).join("　"));
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
    this.day.addEventListener("change", () => this.load());
  }
  open(shop, day) {
    this.shop = shop;
    this.document.querySelector("#history-title").textContent = `${shop.sub_name || shop.name}の履歴`;
    this.day.value = day;
    this.dialog.showModal();
    return this.load();
  }
  async load() {
    const revision = ++this.revision;
    this.body.replaceChildren(element(this.document, "p", "履歴を読み込んでいます"));
    try {
      const data = await this.api.shopHistory(this.shop.id, this.day.value);
      if (revision !== this.revision) return;
      if (!data.observations.length) {
        this.body.replaceChildren(element(this.document, "p", "この日の記録はありません"));
        return;
      }
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
      this.body.replaceChildren(identity,
        plot(this.document, data.observations, "current_waiting", "待ち組数（組）"),
        plot(this.document, data.observations, "official_waiting_minutes", "公式待ち時間（分）"));
    } catch (error) {
      if (revision !== this.revision) return;
      this.body.replaceChildren(element(this.document, "p", error.detail || "履歴を取得できませんでした"));
    }
  }
}
