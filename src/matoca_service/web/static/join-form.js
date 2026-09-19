import {officialEstimate} from "./shop-list.js";
import {trendSection} from "./trends.js";

const confirmationError = "選択内容の確認が必要です";

export class JoinForm {
  constructor(document, api, preferences, queue, onMutation) {
    Object.assign(this, {document, api, preferences, queue, onMutation});
    this.dialog = document.querySelector("#join-dialog");
    this.form = document.querySelector("#join-form");
    this.error = document.querySelector("#join-error");
    this.selected = null;
    this.limits = null;
    this.counts = {adult: 2, child: 0};
    this.choices = [];
    this.blocked = true;
    this.loading = false;
    this.submitting = false;
    this.revision = 0;
    this.automatic = false;
    this.editing = null;
    this.conflict = false;
    this.reloadEdit = document.querySelector("#reload-edit");
    this.reloadEdit.addEventListener("click", () => this.reviewEdit());
    this.information = document.querySelector("#in-advance-information");
    this.arrival = document.querySelector("#arrival-at");
    this.consent = document.querySelector("#automation-consent");
    this.mode = document.querySelector("#automation-mode");
    this.margin = document.querySelector("#model-error");
    this.applyTrend = document.querySelector("#apply-trend-margin");
    this.trend = null;
    this.trendApplied = false;
    this.margin.addEventListener("input", () => {
      this.trendApplied = false;
      this.updateTrendMargin();
    });
    this.applyTrend.addEventListener("click", () => {
      if (this.applyTrend.disabled) return;
      const baseline = Number(this.margin.value);
      const addition = this.trend.suggested_addition_minutes;
      this.margin.value = String(Math.min(120, baseline + addition));
      this.trendApplied = true;
      this.appliedBaseline = baseline;
      this.updateTrendMargin(baseline);
    });
    this.mode.addEventListener("change", () => {
      this.consent.checked = false;
      this.updateMode();
      this.sync();
    });
    this.arrival.addEventListener("input", () => this.sync());
    this.consent.addEventListener("change", () => this.sync());
    this.dialog.addEventListener("close", () => { this.revision++; });
    this.form.addEventListener("submit", (event) => { event.preventDefault(); return this.submit(); });
    this.form.querySelectorAll("[data-step]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!this.limits || this.loading || this.submitting) return;
        const type = button.dataset.step;
        this.counts[type] = this.clamp(type, this.counts[type] + Number(button.dataset.delta));
        this.sync();
      });
    });
  }

  clamp(type, count) {
    return Math.max(this.limits[`min_${type}`], Math.min(this.limits[`max_${type}`], count));
  }

  updateTrendMargin(baseline = this.trendApplied ? this.appliedBaseline : Number(this.margin.value)) {
    const addition = this.trend?.suggested_addition_minutes;
    const valid = this.margin.value !== "" && Number.isInteger(baseline) && baseline >= 0 && baseline <= 120;
    this.applyTrend.disabled = this.loading || this.submitting || this.trendApplied || !valid || addition == null;
    this.document.querySelector("#trend-margin-preview").textContent = addition == null ? ""
      : `基準${baseline}分 + 追加${addition}分 → ${Math.min(120, baseline + addition)}分（上限120分）${this.trendApplied ? "・この設定に適用済み" : "・適用ボタンで反映します"}。保存済みの設定は変更しません。`;
  }

  async loadTrend(shopId, revision) {
    let trend = null;
    try { trend = await this.api.shopTrend(shopId); } catch {}
    if (revision !== this.revision || !this.dialog.open) return;
    this.trend = trend;
    this.document.querySelector("#automation-trend").replaceChildren(trendSection(this.document, trend));
    this.updateTrendMargin();
  }

  updateMode() {
    const simulation = this.mode.value === "simulation";
    this.document.querySelector("#simulation-note").hidden = !simulation;
    this.document.querySelector("#automation-explanation").textContent = simulation
      ? "1分ごとに受付条件を確認して記録します。最初の条件成立、または到着予定の2分後に終了します。実際の申込は行いません。受付終了時刻は予測しません。"
      : "1分ごとに評価し、条件が整えば追加の確認なしで申し込みます。到着予定の2分後までに受付できなければ終了します。受付終了時刻は予測しません。";
    this.document.querySelector("#automation-consent-text").textContent = simulation
      ? "実際の申込を行わず、受付条件の確認と記録を開始することに同意します"
      : "条件が整ったら、追加の確認なしで順番待ちを申し込むことに同意します";
    this.form.querySelector('[type="submit"]').textContent = this.automatic
      ? this.editing ? "変更を保存する" : simulation ? "シミュレーションを開始する" : "自動受付を有効にする"
      : "この内容で順番待ちを申し込む";
  }

  async openEdit(task) {
    if (this.submitting || this.dialog.open) return;
    const context = await this.api.automationEditContext(task.id);
    if (this.dialog.open) return;
    return this.open(context.shop, true, context);
  }

  editStatus(task) {
    const status = this.document.querySelector("#automation-edit-status");
    status.hidden = !task;
    if (task) status.textContent = `${task.mode === "simulation" ? "シミュレーション（申込なし）" : "実際に自動受付する"}・${task.last_decision}・次回評価 ${task.next_evaluation_at ? new Date(task.next_evaluation_at).toLocaleString("ja-JP") : "未定"}。保存後すぐに再評価します。`;
  }

  async reviewEdit() {
    if (!this.editing || this.loading || this.submitting) return;
    const revision = this.revision;
    this.loading = true;
    this.sync();
    const draft = {...this.editing, adult_count: this.counts.adult, child_count: this.counts.child};
    for (const {select, answer} of this.choices) draft[`answer${answer}`] = select.value === "" ? null : Number(select.value);
    try {
      const context = await this.api.automationEditContext(this.editing.id);
      if (revision !== this.revision || !this.dialog.open) return;
      const compatible = context.form_revision === this.formRevision;
      this.editing = context.task;
      this.formRevision = context.form_revision;
      this.choices = [];
      this.configure(context.shop.forms, null, draft, compatible);
      this.conflict = false;
      this.reloadEdit.hidden = true;
      this.consent.checked = false;
      this.editStatus(context.task);
      this.error.textContent = compatible
        ? "最新情報を読み込みました。入力は保持しています。内容を確認して再度同意してください"
        : "受付の質問が変わりました。人数と日時の入力は保持しています。選択内容を確認し、改めて選んでください";
    } catch (error) {
      if (revision === this.revision) this.error.textContent = error.detail || "最新情報を取得できませんでした。入力は保持しています";
    } finally {
      if (revision === this.revision) { this.loading = false; this.sync(); }
    }
  }

  async open(shop, automatic = false, context = null) {
    if (this.submitting || (automatic ? shop.stale : shop.can_join !== true || !this.queue.canJoin)) return;
    this.automatic = automatic;
    this.editing = context?.task || null;
    this.formRevision = context?.form_revision;
    this.conflict = false;
    this.reloadEdit.hidden = true;
    this.mode.value = this.editing?.mode || "live";
    this.mode.disabled = Boolean(this.editing);
    this.editStatus(this.editing);
    this.document.querySelector("#automation-fields").hidden = !automatic;
    this.arrival.required = automatic;
    this.consent.required = automatic;
    this.consent.checked = false;
    this.arrival.value = "";
    if (this.editing) {
      const arrival = new Date(this.editing.arrival_at);
      this.arrival.value = new Date(arrival.getTime() - arrival.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    }
    this.information.value = this.editing?.in_advance_information || "";
    this.document.querySelector("#early-tolerance").value = String(this.editing?.early_tolerance_minutes ?? 15);
    this.margin.value = String(this.editing?.model_error_minutes ?? 15);
    this.trend = null;
    this.trendApplied = false;
    this.document.querySelector("#automation-trend").textContent = "公式目安の変動を読み込んでいます";
    this.updateTrendMargin();
    this.form.querySelector('[type="submit"]').textContent = automatic ? "自動受付を有効にする" : "この内容で順番待ちを申し込む";
    this.updateMode();
    const revision = ++this.revision;
    this.selected = shop;
    this.limits = null;
    this.choices = [];
    this.blocked = true;
    this.loading = true;
    this.error.textContent = "";
    this.document.querySelector("#join-shop-name").textContent = shop.sub_name || shop.name;
    this.document.querySelector("#join-status").textContent = "受付に必要な情報を取得しています";
    this.document.querySelector("#confirm-items").replaceChildren();
    this.dialog.showModal();
    this.sync();
    if (automatic) this.loadTrend(shop.id, revision);
    try {
      // Capture defaults for this opening; later settings saves do not rewrite it.
      const [detail, defaults] = context ? [context.shop, null]
        : await Promise.all([this.api.shopDetail(shop.id), this.preferences.defaults()]);
      if (revision !== this.revision || !this.dialog.open) return;
      this.document.querySelector("#join-shop-name").textContent = detail.sub_name || detail.name;
      this.document.querySelector("#join-status").textContent = `${detail.current_waiting ?? "—"}組待ち・公式目安 ${officialEstimate(detail.waiting_time?.minutes, detail.waiting_time?.is_more)}`;
      this.configure(detail.forms, defaults, this.editing, context?.selections_compatible);
      if (automatic && shop.prediction) {
        this.document.querySelector("#join-status").textContent += `・予測 ${shop.prediction.fast_minutes}〜${shop.prediction.typical_minutes}分`;
      }
    } catch (error) {
      if (revision !== this.revision) return;
      this.error.textContent = error.detail || "受付に必要な情報を取得できませんでした";
    } finally {
      if (revision === this.revision) {
        this.loading = false;
        this.sync();
      }
    }
  }

  configure(forms, defaults, saved = null, compatible = true) {
    if (!forms || ["adult", "child"].some((type) =>
      !Number.isInteger(forms[`min_${type}`]) || !Number.isInteger(forms[`max_${type}`])
      || forms[`min_${type}`] < 0 || forms[`max_${type}`] < forms[`min_${type}`])) {
      this.error.textContent = "受付に必要な情報を取得できませんでした";
      return;
    }
    this.limits = {...forms};
    const childHidden = forms.max_child === 0;
    this.document.querySelector("#child-count").closest(".counter-row").hidden = childHidden && !(saved?.child_count > 0);
    if (childHidden) this.limits.max_child = this.limits.min_child = 0;
    this.counts = saved ? {adult: saved.adult_count, child: saved.child_count}
      : {adult: this.clamp("adult", defaults.default_adult_count),
        child: this.clamp("child", defaults.default_child_count)};
    this.blocked = Boolean(forms.is_confirm_tel || (childHidden && forms.min_child > 0));
    this.renderConfirmations(forms.confirm_items ?? [], saved, compatible);
    if (this.blocked) this.error.textContent = confirmationError;
    else if (saved && ["adult", "child"].some((type) => this.counts[type] !== this.clamp(type, this.counts[type]))) {
      this.error.textContent = "人数の受付範囲が変わりました。保存済みの人数を表示しています。人数を調整してください";
    }
  }

  renderConfirmations(items, saved = null, compatible = true) {
    const target = this.document.querySelector("#confirm-items");
    target.replaceChildren();
    if (!Array.isArray(items)) { this.blocked = true; return; }
    items.forEach((item, index) => {
      if (!item || typeof item.enable !== "boolean") { this.blocked = true; return; }
      if (!item.enable) return;
      if (index > 1 || !Array.isArray(item.sub_items) || typeof item.title !== "string" || !item.title.trim()) {
        this.blocked = true;
        return;
      }
      const options = item.sub_items.filter((option) => option?.enable === true && !option.disabled);
      if (!options.length || options.some((option) => !Number.isInteger(option.sub_item_index) || option.sub_item_index < 0
        || typeof option.text !== "string" || !option.text.trim())
        || new Set(options.map((option) => option.sub_item_index)).size !== options.length) {
        this.blocked = true;
        return;
      }
      const label = this.document.createElement("label");
      label.textContent = item.title;
      const select = this.document.createElement("select");
      select.dataset.answer = String(index + 1);
      if (options.length > 1 || saved) {
        const prompt = this.document.createElement("option");
        prompt.value = "";
        prompt.textContent = "選択してください";
        prompt.disabled = true;
        select.append(prompt);
      }
      for (const option of options) {
        const node = this.document.createElement("option");
        node.value = String(option.sub_item_index);
        node.textContent = option.text;
        select.append(node);
      }
      select.value = saved ? compatible && saved[`answer${index + 1}`] != null ? String(saved[`answer${index + 1}`]) : ""
        : options.length === 1 ? String(options[0].sub_item_index) : "";
      this.choices.push({select, values: options.map((option) => String(option.sub_item_index)), answer: index + 1});
      select.addEventListener("change", () => {
        this.error.textContent = this.blocked || !this.answersReady() ? confirmationError : "";
        this.sync();
      });
      label.append(select);
      target.append(label);
    });
    if (!this.answersReady()) this.error.textContent = confirmationError;
  }

  answersReady() { return this.choices.every(({select, values}) => values.includes(select.value)); }

  ready() {
    if (this.conflict || !this.limits || ["adult", "child"].some((type) =>
      this.counts[type] < this.limits[`min_${type}`] || this.counts[type] > this.limits[`max_${type}`])) return false;
    if (!this.automatic) return this.selected?.can_join === true && this.queue.canJoin;
    return !this.selected?.stale && this.consent.checked && Number.isFinite(new Date(this.arrival.value).getTime())
      && new Date(this.arrival.value).getTime() > Date.now();
  }

  updateCatalog(shops) {
    if (this.selected && !this.editing) this.selected = shops.find((shop) => shop.id === this.selected.id)
      || {...this.selected, can_join: false};
    this.sync();
  }

  sync() {
    const busy = this.loading || this.submitting;
    this.reloadEdit.disabled = busy;
    this.updateTrendMargin();
    this.form.setAttribute("aria-busy", String(busy));
    for (const type of ["adult", "child"]) this.document.querySelector(`#${type}-count`).textContent = this.counts[type];
    this.form.querySelectorAll("[data-step]").forEach((button) => {
      const type = button.dataset.step, delta = Number(button.dataset.delta);
      const next = this.limits ? this.clamp(type, this.counts[type] + delta) : this.counts[type];
      button.disabled = busy || !this.limits || (delta > 0 ? next <= this.counts[type] : next >= this.counts[type]);
    });
    for (const {select} of this.choices) select.disabled = busy;
    this.form.querySelector('[type="submit"]').disabled = busy || this.blocked || !this.answersReady()
      || !this.ready();
  }

  async submit() {
    if (!this.dialog.open || this.loading || this.submitting || this.blocked || !this.answersReady()
      || !this.ready()) return;
    this.submitting = true;
    if (!this.automatic) this.queue.beginMutation();
    this.error.textContent = "";
    this.sync();
    const body = {shop_id: this.selected.id, adult_count: this.counts.adult, child_count: this.counts.child,
      answer1: 0, answer2: null, in_advance_information: this.information.value};
    for (const {select, answer} of this.choices) body[`answer${answer}`] = Number(select.value);
    let succeeded = false;
    try {
      if (this.automatic) {
        const values = {...body, arrival_at: new Date(this.arrival.value).toISOString(),
          timezone: this.api.timezone, consent: true,
          early_tolerance_minutes: Number(this.document.querySelector("#early-tolerance").value),
          model_error_minutes: Number(this.document.querySelector("#model-error").value)};
        if (this.editing) {
          delete values.shop_id;
          await this.api.editAutomation(this.editing.id, {...values,
            expected_version: this.editing.version, form_revision: this.formRevision});
        } else await this.api.createAutomation({...values, mode: this.mode.value});
        this.dialog.close();
        succeeded = true;
      } else {
      const item = await this.api.createWaiting(body);
      const observedAt = new Date().toISOString();
      const called = item.count === 0;
      this.queue.finishMutation([{
        ...item,
        waiting_id: item.id,
        shop_id: item.shop_id ?? this.selected.id,
        status: called ? "called" : "active",
        source: "manual",
        submitted_at: observedAt,
        official_minutes_at_submission: this.selected.waiting_time?.minutes ?? null,
        official_is_more_at_submission: this.selected.waiting_time?.is_more ?? null,
        called_at: called ? observedAt : null,
        observations: [{observed_at: observedAt, count: item.count ?? null}],
      }]);
      this.dialog.close();
      succeeded = true;
      }
    } catch (error) {
      if (!this.automatic) this.queue.finishMutation();
      this.error.textContent = error.detail || "順番待ちの申し込みに失敗しました";
      if (this.editing && error.status === 409) {
        this.conflict = true;
        this.reloadEdit.hidden = false;
        this.error.textContent += "。入力は保持しています。最新情報を読み込んで確認してください";
      }
    } finally {
      this.submitting = false;
      this.sync();
    }
    if (succeeded) await this.onMutation();
  }
}
