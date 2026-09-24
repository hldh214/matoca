import {feedback} from "./feedback.js";
import {officialEstimate} from "./shop-list.js";

const labels = {scheduled: "監視待ち", monitoring: "監視中", submitting: "送信中", reconciling: "受付結果を確認中",
  queued: "受付済み", completed: "呼出済み", cancelled: "停止済み", expired: "期限切れ", failed: "受付失敗",
  needs_attention: "確認が必要", unknown: "結果を確認してください"};

export class AutomationPanel {
  constructor(document, api, onEdit) {
    Object.assign(this, {document, api, onEdit});
    this.target = document.querySelector("#automation-tasks");
    this.error = document.querySelector("#automation-error");
    this.items = [];
    this.loading = false;
    this.busy = false;
    this.revision = 0;
    this.known = false;
  }

  node(tag, text, className = "") {
    const node = this.document.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  }

  time(value) {
    return value ? new Date(value).toLocaleString("ja-JP", {timeZone: this.api.timezone,
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"}) : "未確認";
  }

  async refresh(force = false) {
    if ((this.loading && !force) || this.busy) return;
    const revision = ++this.revision;
    this.loading = true;
    try {
      const items = await this.api.tasks();
      if (!Array.isArray(items)) throw new Error();
      if (revision !== this.revision) return;
      this.known = true;
      this.items = items.filter((item) => item.merchant_key === decodeURIComponent(this.api.merchantKey));
      this.error.textContent = "";
      this.render();
    } catch {
      if (revision !== this.revision) return;
      if (!this.known) this.target.textContent = "予定を取得できませんでした";
      this.error.textContent = "自動受付の予定を更新できません。保存済みの表示です。最新情報に更新して再確認してください";
    } finally { if (revision === this.revision) this.loading = false; }
  }

  render() {
    this.target.replaceChildren();
    if (!this.items.length) this.target.append(this.node("p", "自動受付の予定はありません"));
    for (const task of this.items) {
      const card = this.node("article", "", "automation-task");
      card.append(this.node("strong", task.shop_name), this.node("span", labels[task.state] || "確認が必要", "status-pill"));
      card.append(this.node("p", `到着予定 ${this.time(task.arrival_at)}・大人${task.adult_count}人／子ども${task.child_count}人`));
      const check = task.last_check;
      card.append(this.node("p", `前回確認の公式目安 ${officialEstimate(check?.official_minutes, check?.official_is_more)}・確認 ${this.time(task.evaluated_at)}${task.next_evaluation_at ? `・次回 ${this.time(task.next_evaluation_at)}` : ""}`));
      card.append(this.node("p", task.last_decision));
      if (["scheduled", "monitoring", "needs_attention"].includes(task.state) && !task.intent_id) {
        const actions = this.node("div", "", "automation-actions");
        const edit = this.node("button", "設定を変更", "schedule-button");
        const stop = this.node("button", "監視を停止", "schedule-button");
        edit.type = stop.type = "button";
        edit.disabled = stop.disabled = this.busy;
        edit.addEventListener("click", () => this.onEdit(task));
        stop.addEventListener("click", () => this.stop(task.id));
        actions.append(edit, stop);
        card.append(actions);
      }
      this.target.append(card);
    }
  }

  async stop(id) {
    if (this.busy) return;
    this.revision++;
    this.loading = false;
    this.busy = true;
    this.render();
    try {
      const task = await this.api.stopTask(id);
      this.items = this.items.map((item) => item.id === id ? task : item);
      feedback(this.document, "監視を停止しました。受付済みの順番待ちは取り消されません");
    } catch (error) { feedback(this.document, error.detail || "監視を停止できませんでした", true); }
    finally { this.busy = false; this.render(); }
    await this.refresh();
  }
}
