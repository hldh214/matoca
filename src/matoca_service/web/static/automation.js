const labels = {simulated: "条件成立・申込なし", scheduled: "評価待ち", monitoring: "監視中", submitting: "受付送信中",
  reconciling: "受付結果を照合中", queued: "受付済み", completed: "呼び出し確認済み",
  cancelled: "終了", expired: "期限終了", failed: "受付失敗", needs_attention: "確認が必要", unknown: "結果不明"};

export class AutomationPanel {
  constructor(document, api, onMutation, onEdit) {
    Object.assign(this, {document, api, onMutation, onEdit});
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
      if (task.mode === "simulation") title.textContent = `シミュレーション・${title.textContent}`;
      const timing = this.document.createElement("p");
      const time = (value) => value ? new Date(value).toLocaleString("ja-JP") : "—";
      timing.textContent = `到着予定 ${time(task.arrival_at)}・評価 ${time(task.evaluated_at)}・次回評価 ${time(task.next_evaluation_at)}`;
      const decision = this.document.createElement("p");
      decision.textContent = task.last_decision;
      const error = this.document.createElement("p");
      error.className = "form-error";
      error.setAttribute("role", "alert");
      item.append(title, timing, decision);
      const history = this.document.createElement("details");
      const summary = this.document.createElement("summary");
      summary.textContent = "判断履歴を見る";
      const records = this.document.createElement("div");
      history.append(summary, records);
      history.addEventListener("toggle", async () => {
        if (!history.open) return;
        records.textContent = "読み込み中です";
        try {
          const rows = await this.api.automationHistory(task.id);
          records.replaceChildren();
          if (!rows.length) records.textContent = "評価記録はまだありません";
          for (const row of rows) {
            const text = this.document.createElement("p");
            text.textContent = `${time(row.evaluated_at)}・${row.reason}・条件成立 ${row.would_submit ? "はい" : "いいえ"}・最新確認 ${row.fresh ? "有効" : "期限切れ"}・公式 ${row.official_minutes ?? "不明"}${row.official_minutes === null ? "" : "分"}${row.official_is_more ? "以上" : ""}・予測 ${row.prediction ? `${row.prediction.fast_minutes}〜${row.prediction.typical_minutes}分` : "なし"}・早着許容 ${row.early_tolerance_minutes}分・予測誤差 ${row.model_error_minutes}分`;
            records.append(text);
          }
        } catch { records.textContent = "判断履歴を取得できませんでした"; }
      });
      item.append(history);
      const cancellable = !task.intent_id && ["scheduled", "monitoring", "needs_attention"].includes(task.state);
      const resolvable = task.intent_id && ["needs_attention", "reconciling", "submitting"].includes(task.state);
      if (cancellable) {
        const edit = this.document.createElement("button");
        edit.type = "button";
        edit.className = "history-button";
        edit.textContent = "設定を編集";
        edit.addEventListener("click", async () => {
          edit.disabled = true;
          try { await this.onEdit(task); }
          catch (failure) { error.textContent = failure.detail || "編集内容を取得できませんでした"; }
          finally { edit.disabled = false; }
        });
        item.append(edit);
      }
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
