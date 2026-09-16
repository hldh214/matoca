import json
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import quote

from matoca_service.config import MerchantRegistry
from matoca_service.notifications.repository import NotificationRepository, timestamp

MERCHANT_NAMES = {
    key: value.name for key, value in MerchantRegistry.load_builtin().merchants.items()
}


def location(merchant: str, shop: str) -> str:
    return f"{MERCHANT_NAMES.get(merchant, '加盟店')}・{shop}: "


def task_event(connection: sqlite3.Connection, task_id: str, state: str, now: datetime) -> None:
    messages = {
        "needs_attention": ("自動受付の確認が必要です", "受付状況を確認してください"),
        "failed": ("自動受付に失敗しました", "受付状況と設定を確認してください"),
        "expired": ("自動受付の期限が過ぎました", "到着予定の受付期限までに申し込めませんでした"),
    }
    if state not in messages:
        return
    row = connection.execute(
        "SELECT merchant_key, payload FROM automation_tasks WHERE id=?", (task_id,)
    ).fetchone()
    title, body = messages[state]
    NotificationRepository.publish(
        connection,
        f"task:{task_id}:{state}",
        state,
        title,
        location(row[0], str(json.loads(row[1])["shop_name"])) + body,
        f"/merchants/{quote(row[0], safe='')}",
        now,
    )


def queue_observation(
    connection: sqlite3.Connection, session_id: int, now: datetime, count: int | None
) -> None:
    row = connection.execute(
        """SELECT q.merchant_key, q.intent_id, q.source,
        COALESCE(s.name, '店舗' || COALESCE(q.shop_id, '不明'))
        FROM queue_sessions q LEFT JOIN shops s
        ON s.merchant_key=q.merchant_key AND s.shop_id=q.shop_id WHERE session_id=?""",
        (session_id,),
    ).fetchone()
    url = f"/merchants/{quote(row[0], safe='')}"
    prefix = location(row[0], row[3])
    if row[2] == "automation" and row[1]:
        NotificationRepository.publish(
            connection,
            f"submission:{row[1]}",
            "automated_submission",
            "自動受付が完了しました",
            prefix + "現在の順番待ちを確認してください",
            url,
            now,
        )
    if count is None or count < 0:
        return
    for threshold in (10, 5, 0):
        if count <= threshold:
            NotificationRepository.publish(
                connection,
                f"queue:{session_id}:groups:{threshold}",
                f"groups_{threshold}",
                "順番待ちのお知らせ",
                prefix
                + (
                    "待ち組数が0組になりました。店舗の案内を確認してください"
                    if threshold == 0
                    else f"待ち組数が{threshold}組以下になりました"
                ),
                url,
                now,
            )
    observations = connection.execute(
        """SELECT observed_minute, count FROM queue_session_observations
        WHERE session_id=? AND observed_minute<=? AND count IS NOT NULL
        ORDER BY observed_minute DESC LIMIT 2""",
        (session_id, timestamp(now)),
    ).fetchall()
    if len(observations) != 2 or count == 0:
        return
    latest, previous = observations
    elapsed = (
        datetime.fromisoformat(latest[0]) - datetime.fromisoformat(previous[0])
    ).total_seconds()
    drop = previous[1] - latest[1]
    if elapsed > 600:
        connection.execute(
            "DELETE FROM queue_notification_predictions WHERE session_id=?", (session_id,)
        )
        return
    if elapsed <= 0 or drop <= 0:
        return
    target = datetime.fromisoformat(latest[0]) + timedelta(seconds=elapsed * count / drop)
    baseline = connection.execute(
        "SELECT target_at, observed_at FROM queue_notification_predictions WHERE session_id=?",
        (session_id,),
    ).fetchone()
    if baseline and timestamp(now) <= baseline[1]:
        return
    earlier = baseline and datetime.fromisoformat(baseline[0]) - target >= timedelta(minutes=10)
    if earlier:
        NotificationRepository.publish(
            connection,
            f"queue:{session_id}:earlier:{timestamp(target)}",
            "predicted_earlier",
            "呼び出し予測が早まりました",
            prefix + "待ち組数の減少から見た呼び出し予測が10分以上早まりました。"
            "目安として確認してください",
            url,
            now,
        )
    # Keep the last alerted baseline so multiple smaller advances accumulate.
    if not baseline or earlier or target > datetime.fromisoformat(baseline[0]):
        connection.execute(
            """INSERT INTO queue_notification_predictions VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET target_at=excluded.target_at,
            observed_at=excluded.observed_at""",
            (session_id, timestamp(target), timestamp(now)),
        )
