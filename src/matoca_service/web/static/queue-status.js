import {feedback} from "./feedback.js";
import {officialEstimate} from "./shop-list.js";
import {callTime} from "./queue-time.js";

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

  get hasQueue() { return this.items.some((item) => ["active", "called", "pending", "unresolved"].includes(item.status)); }
  get canJoin() { return this.known && !this.hasQueue && !this.busy; }

  async refresh() {
    if (this.busy) return;
    const revision = ++this.revision;
    try {
      const items = await this.api.currentWaiting();
      if (revision !== this.revision) return;
      if (!Array.isArray(items)) throw new Error("Invalid queue response");
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
      if (this.readError) this.addRetry();
      return;
    }
    const item = this.items.find((value) => ["active", "called", "pending", "unresolved"].includes(value.status));
    this.target.replaceChildren();
    if (!item) {
      this.target.textContent = "現在の順番待ちはありません";
    } else {
      const shop = encodeURIComponent(item.merchant_key) === this.api.merchantKey
        ? this.shops.find((value) => String(value.id) === String(item.shop_id)) : null;
      const latest = item.observations?.at(-1);
      const active = this.node("div", "active-queue");
      const officialStatus = Number(latest?.raw_status);
      active.dataset.state = this.readError || item.stale ? "stale"
        : ({8: "soon", 4: "calling", 5: "pending"}[officialStatus] || "waiting");
      const call = this.node("div", "queue-call-time", "呼出目安（公式）");
      call.append(this.node("b", "", callTime({...item, stale: item.stale || this.readError})));
      active.append(call);
      active.append(this.node("strong", "", shop?.sub_name || shop?.name || item.shop_name
        || item.merchant_name || "受付中"));
      const metrics = this.node("div", "queue-metrics");
      const estimate = officialEstimate(latest?.official_minutes, latest?.official_is_more);
      for (const [label, value] of [["受付番号", item.number ?? "—"], ["前の組数", `${latest?.count ?? "—"}組`], ["現在の公式目安", estimate], ["最終更新", latest ? new Date(latest.observed_at).toLocaleTimeString("ja-JP", {hour: "2-digit", minute: "2-digit"}) : "—"]]) {
        const metric = this.node("span", "", label);
        metric.append(this.node("b", "", value));
        metrics.append(metric);
      }
      active.append(metrics);
      const party = this.node("p", "queue-party",
        `大人${item.adult_count ?? "—"}人・子ども${item.child_count ?? "—"}人`);
      const submitted = item.submitted_at ? new Date(item.submitted_at).toLocaleString("ja-JP",
        {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"}) : "不明";
      party.append(`・受付日時 ${submitted}`);
      active.append(party);
      const links = this.node("div", "queue-links");
      if (encodeURIComponent(item.merchant_key) !== this.api.merchantKey) {
        const returnLink = this.node("a", "", "受付中の店舗へ");
        returnLink.href = `/merchants/${encodeURIComponent(item.merchant_key)}`;
        links.append(returnLink);
      }
      if (shop || item.shop_name) {
        const map = this.node("a", "", "地図で見る");
        const query = [shop?.address, item.merchant_name, shop?.sub_name || shop?.name || item.shop_name]
          .filter(Boolean).join(" ");
        map.href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
        map.target = "_blank";
        map.rel = "noopener noreferrer";
        links.append(map);
      }
      active.append(links);
      if (latest?.raw_status !== undefined && latest.raw_status !== null) {
        const state = {2: "順番待ち", 8: "事前呼出", 4: "呼出中", 5: "保留中", 6: "完了"}[latest.raw_status] || "状態確認中";
        active.append(this.node("p", "queue-state", `公式状態：${state}`));
      }
      for (const event of item.milestones || []) {
        const label = {pre_call: "事前呼出", calling: "呼出", cancelled: "取消"}[event.kind];
        active.append(this.node("p", "", `${label} ${new Date(event.occurred_at).toLocaleTimeString("ja-JP", {hour: "2-digit", minute: "2-digit"})}`));
      }
      if (["active", "called"].includes(item.status)) {
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
          this.dialog.dataset.merchantKey = item.merchant_key || decodeURIComponent(this.api.merchantKey);
          this.dialog.showModal();
        });
        active.append(cancel);
      } else {
        const labels = {pending: "受付結果を確認中です", unresolved: "受付結果を確認できません。再申込せず確認してください", called: "呼び出し済み", cancelled: "取消済み", unknown: "結果を確認できません"};
        active.append(this.node("p", "queue-terminal-status", labels[item.status] || "受付終了"));
        if (["pending", "unresolved"].includes(item.status)) {
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
    if (this.readError) {
      this.target.append(this.node("p", "queue-read-error", "順番待ちを更新できませんでした。前回の情報を表示しています"));
      this.addRetry();
    }
  }

  addRetry() {
    const retry = this.node("button", "text-button queue-retry", "再読み込み");
    retry.type = "button";
    retry.addEventListener("click", async () => { retry.disabled = true; await this.refresh(); });
    this.target.append(retry);
  }

  async cancel() {
    const id = this.dialog.dataset.waitingId;
    const intentId = this.dialog.dataset.intentId;
    if (intentId) {
      if (!this.document.querySelector("#resolve-intent-consent").checked
        || !this.items.some((item) => item.intent_id === intentId && ["pending", "unresolved"].includes(item.status))) return;
    } else if (!this.items.some((item) => String(item.waiting_id) === id
      && (item.merchant_key || decodeURIComponent(this.api.merchantKey)) === this.dialog.dataset.merchantKey)) return;
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
        await this.api.cancelWaiting(id, this.dialog.dataset.merchantKey);
        this.finishMutation(this.items.filter((item) => String(item.waiting_id) !== id
          || (item.merchant_key || decodeURIComponent(this.api.merchantKey)) !== this.dialog.dataset.merchantKey));
      }
      this.dialog.close();
      feedback(this.document, intentId ? "受付結果の確認を終了しました" : "順番待ちを取り消しました");
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
