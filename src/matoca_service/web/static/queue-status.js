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
    this.document.querySelector("#resolve-intent-consent").addEventListener("change", (event) => {
      this.form.querySelector('[type="submit"]').disabled = this.busy || !event.target.checked;
    });
  }

  get hasQueue() { return this.items.some((item) => ["active", "pending", "unresolved"].includes(item.status)); }
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
    const item = this.items.find((value) => ["active", "pending", "unresolved"].includes(value.status)) || this.items[0];
    this.target.replaceChildren();
    if (!item) {
      this.target.textContent = "現在の順番待ちはありません";
    } else {
      const shop = encodeURIComponent(item.merchant_key) === this.api.merchantKey
        ? this.shops.find((value) => String(value.id) === String(item.shop_id)) : null;
      const latest = item.observations?.at(-1);
      const active = this.node("div", "active-queue");
      active.append(this.node("strong", "", shop?.sub_name || shop?.name || item.shop_name
        || item.merchant_name || "受付中"));
      const metrics = this.node("div", "queue-metrics");
      const estimate = officialEstimate(item.official_minutes_at_submission, item.official_is_more_at_submission);
      for (const [label, value] of [["受付番号", item.number ?? "—"], ["前の組数", `${latest?.count ?? "—"}組`], ["受付時の公式目安", estimate], ["最終更新", latest ? new Date(latest.observed_at).toLocaleTimeString("ja-JP", {hour: "2-digit", minute: "2-digit"}) : "—"]]) {
        const metric = this.node("span", "", label);
        metric.append(this.node("b", "", value));
        metrics.append(metric);
      }
      if (!item.stale && item.prediction) {
        const confidence = {low: "低", medium: "中", high: "高"}[item.prediction.confidence];
        const metric = this.node("span", "", "残り予測");
        metric.append(this.node("b", "", `${item.prediction.fast_minutes}〜${item.prediction.typical_minutes}分`));
        metric.append(`信頼度 ${confidence}・実効${Number(item.prediction.effective_samples).toFixed(1)}件`);
        metrics.append(metric);
      }
      active.append(metrics);
      if (item.status === "active") {
        const cancel = this.node("button", "text-button", "取消");
        cancel.id = "cancel-button";
        cancel.type = "button";
        cancel.disabled = this.busy;
        cancel.addEventListener("click", () => {
          delete this.dialog.dataset.intentId;
          this.document.querySelector("#resolve-intent-confirmation").hidden = true;
          this.document.querySelector("#cancel-title").textContent = "順番待ちを取り消しますか？";
          this.form.querySelector('[type="submit"]').textContent = "順番待ちを取り消す";
          this.form.querySelector('[type="submit"]').disabled = false;
          this.document.querySelector("#cancel-error").textContent = "";
          this.document.querySelector("#cancel-detail").textContent = `受付番号 ${item.number ?? item.id}`;
          this.dialog.dataset.waitingId = String(item.waiting_id);
          this.dialog.showModal();
        });
        active.append(cancel);
      } else {
        const labels = {pending: "受付結果を確認中です", unresolved: "受付結果を確認できません。再申込せず確認してください", called: "呼び出し済み", cancelled: "取消済み", unknown: "結果を確認できません"};
        active.append(this.node("p", "queue-terminal-status", labels[item.status] || "受付終了"));
        if (["pending", "unresolved"].includes(item.status) && item.source === "manual") {
          const resolve = this.node("button", "text-button", "受付結果を確認して終了");
          resolve.type = "button";
          resolve.disabled = this.busy;
          resolve.addEventListener("click", () => {
            delete this.dialog.dataset.waitingId;
            this.dialog.dataset.intentId = item.intent_id;
            this.document.querySelector("#cancel-title").textContent = "受付結果の確認を終了しますか？";
            this.document.querySelector("#cancel-detail").textContent = "すべての加盟店の受付状況を再確認します。順番待ちの再申込や取消は行いません。";
            this.document.querySelector("#cancel-error").textContent = "";
            this.document.querySelector("#resolve-intent-confirmation").hidden = false;
            this.document.querySelector("#resolve-intent-consent").checked = false;
            this.form.querySelector('[type="submit"]').textContent = "受付がないことを確認して終了";
            this.form.querySelector('[type="submit"]').disabled = true;
            this.dialog.showModal();
          });
          active.append(resolve);
        }
      }
      this.target.append(active);
    }
    if (item?.stale) this.target.append(this.node("p", "queue-read-error", "順番待ちの更新が遅れています。前回の情報を表示しています"));
    if (this.readError) this.target.append(this.node("p", "queue-read-error", "順番待ちを更新できませんでした。前回の情報を表示しています"));
  }

  async cancel() {
    const id = this.dialog.dataset.waitingId;
    const intentId = this.dialog.dataset.intentId;
    if (intentId) {
      if (!this.document.querySelector("#resolve-intent-consent").checked
        || !this.items.some((item) => item.intent_id === intentId && item.source === "manual")) return;
    } else if (!this.items.some((item) => String(item.waiting_id) === id)) return;
    if (!this.beginMutation()) return;
    const errorTarget = this.document.querySelector("#cancel-error");
    errorTarget.textContent = "";
    this.form.setAttribute("aria-busy", "true");
    this.form.querySelector('[type="submit"]').disabled = true;
    let succeeded = false;
    try {
      if (intentId) {
        await this.api.resolveManualIntent(intentId);
        this.finishMutation(this.items.filter((item) => item.intent_id !== intentId));
      } else {
        await this.api.cancelWaiting(id);
        this.finishMutation(this.items.filter((item) => String(item.waiting_id) !== id));
      }
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
