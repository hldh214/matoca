import {officialEstimate} from "./shop-list.js";

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

  async open(shop) {
    if (this.submitting || shop.can_join !== true || !this.queue.canJoin) return;
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
      const [detail, defaults] = await Promise.all([this.api.shopDetail(shop.id), this.preferences.defaults()]);
      if (revision !== this.revision || !this.dialog.open) return;
      this.document.querySelector("#join-shop-name").textContent = detail.sub_name || detail.name;
      this.document.querySelector("#join-status").textContent = `${detail.current_waiting ?? "—"}組待ち・公式目安 ${officialEstimate(detail.waiting_time?.minutes, detail.waiting_time?.is_more)}`;
      this.configure(detail.forms, defaults);
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

  updateCatalog(shops) {
    if (this.selected) this.selected = shops.find((shop) => shop.id === this.selected.id)
      || {...this.selected, can_join: false};
    this.sync();
  }

  sync() {
    const busy = this.loading || this.submitting;
    this.form.setAttribute("aria-busy", String(busy));
    for (const type of ["adult", "child"]) this.document.querySelector(`#${type}-count`).textContent = this.counts[type];
    this.form.querySelectorAll("[data-step]").forEach((button) => {
      const type = button.dataset.step, next = this.counts[type] + Number(button.dataset.delta);
      button.disabled = busy || !this.limits || next < this.limits[`min_${type}`] || next > this.limits[`max_${type}`];
    });
    for (const {select} of this.choices) select.disabled = busy;
    this.form.querySelector('[type="submit"]').disabled = busy || this.blocked || !this.answersReady()
      || this.selected?.can_join !== true || !this.queue.canJoin;
  }

  async submit() {
    if (!this.dialog.open || this.loading || this.submitting || this.blocked || !this.answersReady()
      || this.selected?.can_join !== true || !this.queue.canJoin) return;
    this.submitting = true;
    this.queue.beginMutation();
    this.error.textContent = "";
    this.sync();
    const body = {shop_id: this.selected.id, adult_count: this.counts.adult, child_count: this.counts.child,
      answer1: 0, answer2: null, in_advance_information: ""};
    for (const {select, answer} of this.choices) body[`answer${answer}`] = Number(select.value);
    let succeeded = false;
    try {
      const item = await this.api.createWaiting(body);
      this.queue.finishMutation([item]);
      this.dialog.close();
      succeeded = true;
    } catch (error) {
      this.queue.finishMutation();
      this.error.textContent = error.detail || "順番待ちの申し込みに失敗しました";
    } finally {
      this.submitting = false;
      this.sync();
    }
    if (succeeded) await this.onMutation();
  }
}
