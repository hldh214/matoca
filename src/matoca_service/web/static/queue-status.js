import {officialEstimate} from "./shop-list.js";

export class QueueStatus {
  constructor(document, api, onChange, onMutation) {
    Object.assign(this, {document, api, onChange, onMutation});
    this.target = document.querySelector("#current-queue");
    this.dialog = document.querySelector("#cancel-dialog");
    this.form = document.querySelector("#cancel-form");
    this.items = [];
    this.shops = [];
    this.known = false;
    this.busy = false;
    this.revision = 0;
    this.readError = false;
    this.form.addEventListener("submit", (event) => { event.preventDefault(); return this.cancel(); });
  }

  get hasQueue() { return this.items.length > 0; }
  get canJoin() { return this.known && !this.hasQueue && !this.busy; }

  async refresh() {
    if (this.busy) return;
    const revision = ++this.revision;
    try {
      const items = await this.api.currentWaiting();
      if (revision !== this.revision) return;
      this.items = items;
      this.known = true;
      this.readError = false;
    } catch {
      if (revision !== this.revision) return;
      this.readError = true;
    }
    this.render();
    this.onChange();
  }

  beginMutation() {
    if (this.busy) return false;
    this.busy = true;
    this.revision++;
    this.onChange();
    return true;
  }

  finishMutation(items) {
    this.revision++;
    this.busy = false;
    if (items !== undefined) {
      this.items = items;
      this.known = true;
      this.readError = false;
    }
    this.render();
    this.onChange();
  }

  setCatalog(shops) { this.shops = shops; this.render(); }

  node(tag, className, text) {
    const node = this.document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  render() {
    if (!this.known) {
      this.target.textContent = this.readError ? "順番待ちを確認できませんでした" : "順番待ちを確認中です";
      return;
    }
    const item = this.items[0];
    this.target.replaceChildren();
    if (!item) {
      this.target.textContent = "現在の順番待ちはありません";
    } else {
      const shop = this.shops.find((value) => String(value.id) === String(item.shop_id));
      const active = this.node("div", "active-queue");
      active.append(this.node("strong", "", shop?.sub_name || shop?.name || "受付中"));
      const metrics = this.node("div", "queue-metrics");
      const estimate = item.waiting_time
        ? officialEstimate(item.waiting_time.minutes, item.waiting_time.is_more)
        : officialEstimate(shop?.official_waiting_minutes, shop?.official_waiting_is_more);
      for (const [label, value] of [["受付番号", item.number ?? "—"], ["前の組数", `${item.count ?? "—"}組`], ["公式目安", estimate]]) {
        const metric = this.node("span", "", label);
        metric.append(this.node("b", "", value));
        metrics.append(metric);
      }
      const cancel = this.node("button", "text-button", "取消");
      cancel.id = "cancel-button";
      cancel.type = "button";
      cancel.disabled = this.busy;
      cancel.addEventListener("click", () => {
        this.document.querySelector("#cancel-error").textContent = "";
        this.document.querySelector("#cancel-detail").textContent = `受付番号 ${item.number ?? item.id}`;
        this.dialog.dataset.waitingId = String(item.id);
        this.dialog.showModal();
      });
      active.append(metrics, cancel);
      this.target.append(active);
    }
    if (this.readError) this.target.append(this.node("p", "queue-read-error", "順番待ちを更新できませんでした。前回の情報を表示しています"));
  }

  async cancel() {
    const id = this.dialog.dataset.waitingId;
    if (!this.items.some((item) => String(item.id) === id) || !this.beginMutation()) return;
    const errorTarget = this.document.querySelector("#cancel-error");
    errorTarget.textContent = "";
    this.form.setAttribute("aria-busy", "true");
    this.form.querySelector('[type="submit"]').disabled = true;
    let succeeded = false;
    try {
      await this.api.cancelWaiting(id);
      this.finishMutation(this.items.filter((item) => String(item.id) !== id));
      this.dialog.close();
      succeeded = true;
    } catch (error) {
      this.finishMutation();
      errorTarget.textContent = error.detail || "順番待ちの取消に失敗しました";
    } finally {
      this.form.setAttribute("aria-busy", "false");
      this.form.querySelector('[type="submit"]').disabled = false;
    }
    if (succeeded) await this.onMutation();
  }
}
