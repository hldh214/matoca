import {feedback} from "./feedback.js";
import {officialEstimate} from "./shop-list.js";

const confirmationError = "選択内容の確認が必要です";

function localInput(date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

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
    this.information = document.querySelector("#in-advance-information");
    this.arrival = document.querySelector("#arrival-at");
    this.addTen = document.querySelector("#arrival-add-ten");
    this.subtractTen = document.querySelector("#arrival-subtract-ten");
    for (const [button, minutes] of [[this.addTen, 10], [this.subtractTen, -10]]) {
      button.addEventListener("click", () => {
        const current = new Date(this.arrival.value);
        if (this.loading || this.submitting || !Number.isFinite(current.getTime())) return;
        this.arrival.value = localInput(new Date(current.getTime() + minutes * 60000));
        this.sync();
      });
    }
    this.mode = "manual";
    this.editing = null;
    this.formRevision = null;
    this.arrival.addEventListener("input", () => this.sync());
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

  async open(shop, mode = "manual", task = null) {
    if (this.submitting || (mode === "manual" && (shop.can_join !== true || !this.queue.canJoin))
      || !this.queue.known || this.queue.busy) return;
    this.mode = mode;
    this.editing = null;
    this.formRevision = null;
    this.document.querySelector("#arrival-settings").hidden = mode === "manual";
    this.arrival.required = mode !== "manual";
    const openedAt = Date.now();
    this.arrival.value = localInput(new Date(openedAt + 3600000));
    this.document.querySelector("#arrival-timezone").textContent = `時刻の地域：${this.api.timezone}`;
    this.form.querySelector('[type="submit"]').textContent = mode === "manual"
      ? "この内容で順番待ちを申し込む" : task ? "変更を保存" : "自動受付を開始";
    this.information.value = "";
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
    try {
      // Capture defaults for this opening; later settings saves do not rewrite it.
      const [result, defaults] = await Promise.all([
        task ? this.api.editTaskContext(task.id) : this.api.shopDetail(shop.id), this.preferences.defaults()]);
      if (revision !== this.revision || !this.dialog.open) return;
      const detail = task ? result.shop : result;
      this.formRevision = task ? result.form_revision : detail.form_revision;
      this.document.querySelector("#join-shop-name").textContent = detail.sub_name || detail.name;
      this.document.querySelector("#join-status").textContent = `${detail.current_waiting ?? "—"}組待ち・公式目安 ${officialEstimate(detail.waiting_time?.minutes, detail.waiting_time?.is_more)}`;
      this.configure(detail.forms, defaults);
      if (!task && mode !== "manual") {
        const minutes = detail.waiting_time?.minutes;
        const delay = Number.isFinite(minutes) && minutes >= 0 ? Math.max(1, minutes) : 60;
        this.arrival.value = localInput(new Date(openedAt + delay * 60000));
      }
      if (task) {
        this.editing = result;
        this.arrival.value = localInput(new Date(result.task.arrival_at));
        this.information.value = result.task.in_advance_information || "";
        this.counts = {adult: result.task.adult_count, child: result.task.child_count};
        for (const {select, answer} of this.choices) select.value = String(result.task[`answer${answer}`]);
        if (!result.selections_compatible) this.error.textContent = "受付内容が変更されています。最新の選択肢を確認してください";
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

  configure(forms, defaults) {
    if (!forms || ["adult", "child"].some((type) =>
      !Number.isInteger(forms[`min_${type}`]) || !Number.isInteger(forms[`max_${type}`])
      || forms[`min_${type}`] < 0 || forms[`max_${type}`] < forms[`min_${type}`])) {
      this.error.textContent = "受付に必要な情報を取得できませんでした";
      return;
    }
    this.limits = {...forms};
    const childHidden = forms.max_child === 0;
    this.document.querySelector("#child-count").closest(".counter-row").hidden = childHidden;
    if (childHidden) this.limits.max_child = this.limits.min_child = 0;
    this.counts = {adult: this.clamp("adult", defaults.default_adult_count),
        child: this.clamp("child", defaults.default_child_count)};
    this.blocked = Boolean(forms.is_confirm_tel || (childHidden && forms.min_child > 0));
    this.renderConfirmations(forms.confirm_items ?? []);
    if (this.blocked) this.error.textContent = confirmationError;
  }

  renderConfirmations(items) {
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
      if (options.length > 1) {
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
      select.value = options.length === 1 ? String(options[0].sub_item_index) : "";
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
    if (!this.limits || ["adult", "child"].some((type) =>
      this.counts[type] < this.limits[`min_${type}`] || this.counts[type] > this.limits[`max_${type}`])) return false;
    return this.mode === "manual" ? this.queue.canJoin && this.selected?.can_join === true
      : this.queue.known && !this.queue.busy && Number.isFinite(new Date(this.arrival.value).getTime())
        && new Date(this.arrival.value) > new Date();
  }

  updateCatalog(shops) {
    if (this.selected) this.selected = shops.find((shop) => shop.id === this.selected.id)
      || {...this.selected, can_join: false};
    this.sync();
  }

  sync() {
    const busy = this.loading || this.submitting;
    this.arrival.disabled = busy;
    this.addTen.disabled = busy || !Number.isFinite(new Date(this.arrival.value).getTime());
    this.subtractTen.disabled = this.addTen.disabled;
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
    if (this.mode === "manual") this.queue.beginMutation();
    this.error.textContent = "";
    this.sync();
    const body = {shop_id: this.selected.id, adult_count: this.counts.adult, child_count: this.counts.child,
      answer1: 0, answer2: null, in_advance_information: this.information.value};
    for (const {select, answer} of this.choices) body[`answer${answer}`] = Number(select.value);
    let succeeded = false;
    try {
      if (this.mode !== "manual") {
        const values = {...body, arrival_at: new Date(this.arrival.value).toISOString(), timezone: this.api.timezone, consent: true};
        if (this.editing) {
          delete values.shop_id;
          await this.api.editTask(this.editing.task.id, {...values,
            expected_version: this.editing.task.version, form_revision: this.editing.form_revision});
        } else await this.api.createTask({...values, form_revision: this.formRevision});
        this.dialog.close();
        feedback(this.document, this.editing ? "自動受付の設定を更新しました" : "自動受付を開始しました");
        succeeded = true;
      } else {
      const item = await this.api.createWaiting(body);
      const observedAt = new Date().toISOString();
      const called = Number(item.status) === 4;
      this.queue.finishMutation([{
        ...item,
        merchant_key: decodeURIComponent(this.api.merchantKey),
        shop_name: this.selected.sub_name || this.selected.name,
        waiting_id: item.id,
        shop_id: item.shop_id ?? this.selected.id,
        status: called ? "called" : "active",
        source: "manual",
        submitted_at: observedAt,
        official_minutes_at_submission: this.selected.waiting_time?.minutes ?? null,
        official_is_more_at_submission: this.selected.waiting_time?.is_more ?? null,
        called_at: called ? observedAt : null,
        observations: [{observed_at: observedAt, count: item.count ?? null,
          raw_status: item.status, official_minutes: item.estimate_time?.minutes ?? null,
          official_is_more: item.estimate_time?.is_more ?? false}],
      }]);
      this.dialog.close();
      feedback(this.document, `受付が完了しました。受付番号 ${item.number ?? "—"}`);
      succeeded = true;
      }
    } catch (error) {
      if (this.mode === "manual") this.queue.finishMutation();
      this.error.textContent = error.detail || "順番待ちの申し込みに失敗しました";
    } finally {
      this.submitting = false;
      this.sync();
    }
    if (succeeded) await this.onMutation();
  }
}
