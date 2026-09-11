export class PreferencesDialog {
  constructor(document, api) {
    this.document = document;
    this.api = api;
    this.dialog = document.querySelector("#settings-dialog");
    this.form = document.querySelector("#settings-form");
    this.error = document.querySelector("#settings-error");
    this.saved = null;
    this.loading = null;
    this.revision = 0;
    this.busy = false;
    this.saving = false;
    this.ready = false;
    this.counts = {adult: 2, child: 0};
    document.querySelector("#settings-button").addEventListener("click", () => this.open());
    this.dialog.addEventListener("close", () => {
      if (this.saving || this.dialog.open) return;
      this.revision++;
      this.busy = false;
      this.ready = false;
      this.render();
    });
    this.form.addEventListener("submit", (event) => { event.preventDefault(); return this.save(); });
    this.form.querySelectorAll("[data-preference-step]").forEach((button) => {
      button.addEventListener("click", () => {
        if (this.busy || !this.ready) return;
        const type = button.dataset.preferenceStep;
        this.counts[type] = Math.max(0, Math.min(20, this.counts[type] + Number(button.dataset.delta)));
        this.render();
      });
    });
  }

  async defaults() {
    if (this.saved) return {...this.saved};
    if (!this.loading) {
      const revision = this.revision;
      this.loading = this.api.preferences().then((value) => {
        if (revision === this.revision) this.saved = value;
        return value;
      }).finally(() => { this.loading = null; });
    }
    return {...await this.loading};
  }

  async open() {
    if (this.saving || (this.busy && this.dialog.open)) return;
    const revision = ++this.revision;
    this.dialog.showModal();
    this.error.textContent = "";
    this.busy = true;
    this.ready = false;
    this.render();
    try {
      const preferences = await this.api.preferences();
      if (revision !== this.revision || !this.dialog.open) return;
      this.saved = preferences;
      this.counts = {adult: this.saved.default_adult_count, child: this.saved.default_child_count};
      this.ready = true;
    } catch (error) {
      if (revision !== this.revision || !this.dialog.open) return;
      this.error.textContent = error.detail || "設定を取得できませんでした";
    } finally {
      if (revision === this.revision) {
        this.busy = false;
        this.render();
      }
    }
  }

  render() {
    this.form.setAttribute("aria-busy", String(this.busy));
    for (const type of ["adult", "child"]) {
      this.document.querySelector(`#settings-${type}-count`).textContent = this.counts[type];
    }
    this.form.querySelectorAll("[data-preference-step]").forEach((button) => {
      const next = this.counts[button.dataset.preferenceStep] + Number(button.dataset.delta);
      button.disabled = this.busy || !this.ready || next < 0 || next > 20;
    });
    this.form.querySelector('[type="submit"]').disabled = this.busy || !this.ready;
  }

  async save() {
    if (this.busy || !this.ready) return;
    this.busy = true;
    this.saving = true;
    this.revision++;
    this.error.textContent = "";
    this.render();
    try {
      this.saved = await this.api.savePreferences({
        default_adult_count: this.counts.adult, default_child_count: this.counts.child,
      });
      this.dialog.close();
    } catch (error) {
      this.error.textContent = error.detail || "設定を保存できませんでした";
    } finally {
      this.busy = false;
      this.saving = false;
      this.render();
    }
  }
}
