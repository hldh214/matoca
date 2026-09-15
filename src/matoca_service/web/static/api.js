const requestError = "通信に失敗しました。時間をおいてもう一度お試しください";

export class MatocaApiError extends Error {
  constructor(detail, status = 0) {
    const message = typeof detail === "string" && /[ぁ-んァ-ヶ一-龠]/u.test(detail)
      ? detail : requestError;
    super(message);
    this.name = "MatocaApiError";
    this.detail = message;
    this.status = status;
  }
}

export class MatocaApi {
  constructor(merchantKey) {
    this.merchantKey = encodeURIComponent(merchantKey);
    this.base = `/api/merchants/${this.merchantKey}`;
    try {
      this.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Tokyo";
    } catch {
      this.timezone = "Asia/Tokyo";
    }
  }

  async request(url, method = "GET", body) {
    const options = {
      method, mode: "same-origin", credentials: "same-origin",
      headers: {"X-Timezone": this.timezone},
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    try {
      const response = await fetch(url, options);
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        throw new MatocaApiError(error?.detail, response.status);
      }
      return response.status === 204 ? null : await response.json();
    } catch (error) {
      if (error instanceof MatocaApiError) throw error;
      throw new MatocaApiError();
    }
  }

  console() { return this.request(`${this.base}/console`); }
  async currentWaiting() {
    const items = await this.request("/api/queues");
    return items.filter((item) => ["pending", "unresolved"].includes(item.status)
      || encodeURIComponent(item.merchant_key) === this.merchantKey);
  }
  shopDetail(id) { return this.request(`/api/shops/${encodeURIComponent(id)}?merchant=${this.merchantKey}`); }
  createWaiting(body) { return this.request(`${this.base}/waiting`, "POST", body); }
  cancelWaiting(id) { return this.request(`${this.base}/waiting/${encodeURIComponent(id)}`, "DELETE"); }
  automationTasks() { return this.request("/api/automation/tasks"); }
  createAutomation(body) { return this.request("/api/automation/tasks", "POST", {...body, merchant_key: decodeURIComponent(this.merchantKey)}); }
  cancelAutomation(id) { return this.request(`/api/automation/tasks/${encodeURIComponent(id)}`, "DELETE"); }
  resolveAutomation(id) { return this.request(`/api/automation/tasks/${encodeURIComponent(id)}/resolve`, "POST", {confirm_no_queue: true}); }
  resolveManualIntent(id) { return this.request(`/api/queues/intents/${encodeURIComponent(id)}/resolve`, "POST", {confirm_no_queue: true}); }
  preferences() { return this.request("/api/preferences"); }
  savePreferences(body) { return this.request("/api/preferences", "PUT", body); }
  refresh() { return this.request(`${this.base}/refresh`, "POST"); }
  favorites() { return this.request("/api/favorites"); }
  setFavorite(id, enabled) { return this.request(`${this.base}/shops/${encodeURIComponent(id)}/favorite`, "PUT", {enabled}); }
  shopHistory(id, day) { return this.request(`${this.base}/shops/${encodeURIComponent(id)}/history?day=${encodeURIComponent(day)}`); }
}
