const sources = {shop_daypart: "店舗・同じ曜日区分と時間帯", shop: "店舗全体", merchant: "加盟店全体（他店舗を含む補完）"};

export function trendSection(document, trend) {
  const section = document.createElement("section");
  section.setAttribute("aria-label", "公式目安の変動と余裕の提案");
  const add = (tag, text) => {
    const node = document.createElement(tag); node.textContent = text; section.append(node);
  };
  add("h3", "公式目安の変動（実測待ち時間とは別）");
  if (!trend) {
    add("p", "変動の資料を取得できませんでした");
    return section;
  }
  const time = (value) => new Date(value).toLocaleString("ja-JP", {timeZone: "Asia/Tokyo"});
  add("p", `集計時点 ${time(trend.as_of)}（日本時間）・${trend.day_class === "weekend" ? "土日" : "平日"} ${trend.tokyo_bucket_start}〜${trend.tokyo_bucket_start + 3}時`);
  add("p", trend.recent_change_minutes === null
    ? "直近の有効な約5分区間はありません"
    : `直近の有効区間 ${time(trend.recent_start_at)}〜${time(trend.recent_end_at)}: 公式目安 ${trend.recent_change_minutes > 0 ? "+" : ""}${trend.recent_change_minutes}分`);
  for (const scope of trend.scopes) add("p",
    `${sources[scope.source]}: ${scope.sample_count}区間・下方修正 ${scope.downward_count}区間・下方修正の90%点 ${scope.p90_downward_minutes === null ? "不明" : `${scope.p90_downward_minutes}分`}`);
  add("p", `使用する資料: ${sources[trend.source]}`);
  add("p", trend.suggested_addition_minutes === null
    ? `資料不足（${trend.sample_count}区間 / 必要20区間）・追加余裕は提案できません`
    : `提案する追加余裕: ${trend.suggested_addition_minutes}分（非負の下方修正の90%点）`);
  add("p", trend.limitation);
  return section;
}
