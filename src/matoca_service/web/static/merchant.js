import {MatocaApi} from "./api.js";
import {ShopList} from "./shop-list.js";
import {QueueStatus} from "./queue-status.js";
import {JoinForm} from "./join-form.js";
import {PreferencesDialog} from "./preferences.js";

const api = new MatocaApi(document.body.dataset.merchantKey);
const preferences = new PreferencesDialog(document, api);
const queue = new QueueStatus(document, api, render, reload);
const join = new JoinForm(document, api, preferences, queue, reload);
const list = new ShopList(document, (shop) => join.open(shop));
const search = document.querySelector("#shop-search");
const updated = document.querySelector("#updated-at");
let data = null;
let filter = "available";
let consoleRevision = 0;

function render() {
  list.render(data, filter, search.value, queue.known && !queue.busy, queue.hasQueue);
  join.sync();
}

async function loadConsole() {
  const revision = ++consoleRevision;
  try {
    const current = await api.console();
    if (revision !== consoleRevision) return;
    data = current;
    queue.setCatalog(data.shops);
    join.updateCatalog(data.shops);
    render();
    const time = new Date(data.updated_at).toLocaleTimeString("ja-JP", {hour: "2-digit", minute: "2-digit"});
    updated.textContent = `最終更新 ${time}${data.stale ? "・保存済みデータを表示中" : ""}`;
  } catch {
    if (revision !== consoleRevision) return;
    if (!data) list.error();
    updated.textContent = "最新情報を取得できませんでした";
  }
}

function reload() { return Promise.all([loadConsole(), queue.refresh()]); }

document.querySelectorAll(".dialog-close").forEach((button) => {
  button.addEventListener("click", () => button.closest("dialog").close());
});
document.querySelectorAll("[data-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    filter = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((item) => {
      item.classList.toggle("is-active", item === button);
      item.setAttribute("aria-pressed", String(item === button));
    });
    render();
  });
  button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
});
search.addEventListener("input", render);
document.querySelector("#refresh-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  if (button.disabled) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await api.refresh();
    await loadConsole();
  } catch (error) {
    updated.textContent = error.detail || "最新情報を取得できませんでした";
  } finally {
    button.disabled = false;
    button.setAttribute("aria-busy", "false");
  }
});
setInterval(() => { if (!document.hidden) return reload(); }, 30000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) return reload(); });
reload();
