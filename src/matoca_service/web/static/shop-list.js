export function officialEstimate(minutes, isMore = false) {
  if (!Number.isFinite(minutes)) return "—";
  return isMore ? `${minutes}分以上` : `約${minutes}分`;
}

export class ShopList {
  constructor(document, onJoin, onFavorite, onHistory) {
    this.document = document;
    this.target = document.querySelector("#shop-list");
    this.onJoin = onJoin;
    this.onFavorite = onFavorite;
    this.onHistory = onHistory;
  }

  node(tag, className, text) {
    const element = this.document.createElement(tag);
    element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  render(data, filter, query, sort, favorites, pendingFavorites, queueKnown, hasQueue) {
    if (!data) return;
    this.document.querySelector("#available-count").textContent = data.available_count;
    this.document.querySelector("#total-count").textContent = data.total_count;
    const search = query.trim().toLocaleLowerCase("ja-JP");
    const value = (shop) => sort === "waiting" ? shop.current_waiting
      : sort === "official" ? shop.official_waiting_minutes : null;
    const shops = data.shops.filter((shop) =>
      (filter === "all" || shop.can_join === true)
      && [shop.name, shop.sub_name, shop.address].join(" ").toLocaleLowerCase("ja-JP").includes(search))
      .sort((left, right) => {
        const favoriteOrder = Number(favorites.has(right.id)) - Number(favorites.has(left.id));
        if (favoriteOrder) return favoriteOrder;
        const a = value(left), b = value(right);
        if (a === null && b !== null) return 1;
        if (a !== null && b === null) return -1;
        if (a !== null && b !== null && a !== b) return a - b;
        return (left.sub_name || left.name).localeCompare(right.sub_name || right.name, "ja-JP");
      });
    this.target.replaceChildren();
    if (!shops.length) {
      this.target.append(this.node("p", "empty-state", filter === "available" && !search
        ? "現在、受付可能な店舗はありません" : "条件に一致する店舗はありません"));
    }
    for (const shop of shops) {
      const row = this.node("article", "shop-row");
      row.dataset.id = String(shop.id);
      const identity = this.node("div", "shop-identity");
      if (shop.image_url) {
        const image = this.node("img", "");
        image.src = shop.image_url;
        image.alt = "";
        image.loading = "lazy";
        identity.append(image);
      }
      const description = this.node("div", "");
      description.append(this.node("h3", "", shop.sub_name || shop.name),
        this.node("p", "", shop.address || shop.name));
      identity.append(description);
      const status = this.node("span", "status-pill", shop.status_label);
      status.classList.toggle("available", shop.status === "available");
      const waiting = this.node("span", "metric");
      waiting.append(this.node("strong", "", shop.current_waiting ?? "—"), "組");
      const estimate = this.node("span", "metric");
      estimate.append(this.node("strong", "", officialEstimate(
        shop.official_waiting_minutes, shop.official_waiting_is_more)));
      const action = this.node("button", "join-button", !queueKnown ? "順番待ちを確認中"
        : hasQueue ? "順番待ち受付中" : shop.can_join === true ? "今すぐ受付" : "受付できません");
      action.type = "button";
      action.disabled = shop.can_join !== true || !queueKnown || hasQueue;
      action.addEventListener("click", () => {
        if (!action.disabled) return this.onJoin(shop);
      });
      const tools = this.node("div", "shop-tools");
      const favorite = this.node("button", "favorite-button", favorites.has(shop.id) ? "★" : "☆");
      favorite.type = "button";
      favorite.disabled = pendingFavorites.has(shop.id);
      favorite.setAttribute("aria-label", favorites.has(shop.id) ? "お気に入りから削除" : "お気に入りに追加");
      favorite.addEventListener("click", () => this.onFavorite(shop));
      const history = this.node("button", "history-button", "履歴を見る");
      history.type = "button";
      history.addEventListener("click", () => this.onHistory(shop));
      tools.append(favorite, history);
      row.append(identity, status, waiting, estimate, tools, action);
      this.target.append(row);
    }
  }

  error() {
    this.target.replaceChildren(this.node("p", "empty-state", "最新情報を取得できませんでした"));
  }
}
