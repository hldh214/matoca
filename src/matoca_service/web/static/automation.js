const labels = {scheduled: "評価待ち", monitoring: "監視中", submitting: "受付送信中",
  reconciling: "受付結果を照合中", queued: "受付済み", completed: "呼び出し確認済み",
  cancelled: "終了", expired: "期限終了", failed: "受付失敗", needs_attention: "確認が必要", unknown: "結果不明"};

export class AutomationPanel {
  constructor(document, api, onMutation) {
    Object.assign(this, {document, api, onMutation});
    this.target = document.querySelector("#automation-tasks");
    this.revision = 0;
    this.busy = new Set();
  }

  async refresh() {
    const revision = ++this.revision;
    try {
      const tasks = await this.api.automationTasks();
      if (revision !== this.revision) return;
      this.render(tasks.filter((task) => encodeURIComponent(task.merchant_key) === this.api.merchantKey));
    } catch {
      if (revision === this.revision) this.target.textContent = "自動受付を確認できませんでした";
    }
  }

  render(tasks) {
    this.target.replaceChildren();
    if (!tasks.length) this.target.textContent = "自動受付は設定されていません";
    for (const task of tasks) {
      const item = this.document.createElement("article");
      item.className = "automation-task";
      const title = this.document.createElement("strong");
      title.textContent = `${task.shop_name}・${labels[task.state] || "状態を確認中"}`;
      const timing = this.document.createElement("p");
      const time = (value) => value ? new Date(value).toLocaleString("ja-JP") : "—";
      timing.textContent = `到着予定 ${time(task.arrival_at)}・評価 ${time(task.evaluated_at)}・次回評価 ${time(task.next_evaluation_at)}`;
      const decision = this.document.createElement("p");
      decision.textContent = task.last_decision;
      const error = this.document.createElement("p");
      error.className = "form-error";
      error.setAttribute("role", "alert");
      item.append(title, timing, decision);
      const cancellable = !task.intent_id && ["scheduled", "monitoring", "needs_attention"].includes(task.state);
      const resolvable = task.intent_id && ["needs_attention", "reconciling", "submitting"].includes(task.state);
      if (cancellable || resolvable) {
        const button = this.document.createElement("button");
        button.type = "button";
        button.className = "history-button";
        button.textContent = cancellable ? "監視を取り消す" : "受付がないことを確認して監視を終了";
        button.disabled = this.busy.has(task.id);
        let confirmation = null;
        if (resolvable) {
          const label = this.document.createElement("label");
          confirmation = this.document.createElement("input");
          confirmation.type = "checkbox";
          label.append(confirmation, "現在の受付がないことを確認しました。受付状況を再確認して監視を終了します");
          item.append(label);
          button.disabled = true;
          confirmation.addEventListener("change", () => { button.disabled = !confirmation.checked || this.busy.has(task.id); });
        }
        button.addEventListener("click", async () => {
          if (this.busy.has(task.id) || (confirmation && !confirmation.checked)) return;
          this.busy.add(task.id);
          button.disabled = true;
          ++this.revision;
          try {
            await (cancellable ? this.api.cancelAutomation(task.id) : this.api.resolveAutomation(task.id));
            await this.onMutation();
          } catch (failure) { error.textContent = failure.detail || "操作を完了できませんでした"; }
          finally { this.busy.delete(task.id); button.disabled = false; }
        });
        item.append(button);
      }
      item.append(error);
      this.target.append(item);
    }
  }
}
