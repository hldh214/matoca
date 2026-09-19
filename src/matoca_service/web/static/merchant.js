import {MatocaApi} from "./api.js";
import {ShopList} from "./shop-list.js";
import {QueueStatus} from "./queue-status.js";
import {JoinForm} from "./join-form.js";
import {PreferencesDialog} from "./preferences.js";
import {ShopHistoryDialog} from "./shop-history.js";
import {AutomationPanel} from "./automation.js";

const api = new MatocaApi(document.body.dataset.merchantKey);
const preferences = new PreferencesDialog(document, api);
const queue = new QueueStatus(document, api, render, reload);
const join = new JoinForm(document, api, preferences, queue, reload);
const history = new ShopHistoryDialog(document, api);
const automation = new AutomationPanel(document, api, reload, (task) => join.openEdit(task));
const favorites = new Set();
const pendingFavorites = new Set();
const list = new ShopList(document, (shop) => join.open(shop), toggleFavorite,
  (shop) => history.open(shop, new Date(data.updated_at).toLocaleDateString("sv-SE",
    {timeZone: "Asia/Tokyo"})), (shop) => join.open(shop, true));
const search = document.querySelector("#shop-search");
const sort = document.querySelector("#shop-sort");
const updated = document.querySelector("#updated-at");
let data = null;
let filter = "available";
let consoleRevision = 0;

function render() {
  list.render(data, filter, search.value, sort.value, favorites, pendingFavorites,
    queue.known && !queue.busy, queue.hasQueue);
  join.sync();
}
async function toggleFavorite(shop) {
  if (pendingFavorites.has(shop.id)) return;
  const enabled = !favorites.has(shop.id);
  pendingFavorites.add(shop.id);
  enabled ? favorites.add(shop.id) : favorites.delete(shop.id);
  render();
  try { await api.setFavorite(shop.id, enabled); }
  catch { enabled ? favorites.delete(shop.id) : favorites.add(shop.id); }
  finally { pendingFavorites.delete(shop.id); render(); }
}
async function loadFavorites() {
  const stored = await api.favorites();
  favorites.clear();
  for (const id of stored[document.body.dataset.merchantKey] || []) favorites.add(id);
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

function reload() { return Promise.all([loadConsole(), queue.refresh(), automation.refresh()]); }

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
sort.addEventListener("change", render);
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
loadFavorites().then(reload, reload);
