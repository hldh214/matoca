import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from matoca_service.notifications.models import Delivery, Notification, PushSubscription
from matoca_service.storage.database import Database


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class NotificationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def subscribe(self, subscription: PushSubscription, now: datetime) -> str:
        def write(connection: sqlite3.Connection) -> str:
            existing = connection.execute(
                "SELECT id FROM push_subscriptions WHERE endpoint=?", (subscription.endpoint,)
            ).fetchone()
            identity = str(existing[0]) if existing else str(uuid.uuid4())
            connection.execute(
                """INSERT INTO push_subscriptions VALUES (?, ?, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET payload=excluded.payload,
                updated_at=excluded.updated_at""",
                (identity, subscription.endpoint, subscription.model_dump_json(), timestamp(now)),
            )
            return identity

        return self.database.write(write)

    def unsubscribe(self, endpoint: str) -> None:
        self.database.write(
            lambda c: c.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        )

    def test(self, subscription_id: str, now: datetime) -> None:
        def write(connection: sqlite3.Connection) -> None:
            if (
                connection.execute(
                    "SELECT 1 FROM push_subscriptions WHERE id=?", (subscription_id,)
                ).fetchone()
                is None
            ):
                raise LookupError("このブラウザーの通知を有効にしてください")
            self.publish(
                connection,
                str(uuid.uuid4()),
                "test",
                "通知のテスト",
                "このブラウザーの通知を確認しています",
                "/",
                now,
                subscription_id,
            )

        self.database.write(write)

    @staticmethod
    def publish(
        connection: sqlite3.Connection,
        key: str,
        kind: str,
        title: str,
        body: str,
        url: str,
        now: datetime,
        target: str | None = None,
    ) -> None:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO notification_outbox
            (event_key, kind, title, body, url, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (key, kind, title, body, url, timestamp(now)),
        )
        if cursor.rowcount == 0:
            return
        connection.execute(
            """INSERT INTO notification_deliveries
            (notification_id, subscription_id, next_attempt_at)
            SELECT ?, id, ? FROM push_subscriptions WHERE (? IS NULL OR id=?)""",
            (cursor.lastrowid, timestamp(now), target, target),
        )

    def history(self) -> list[Notification]:
        return self.database.read(
            lambda c: [
                self._notification(r)
                for r in c.execute(
                    "SELECT id, kind, title, body, url, created_at FROM notification_outbox "
                    "ORDER BY id DESC LIMIT 100"
                )
            ]
        )

    @staticmethod
    def _notification(row: tuple[object, ...]) -> Notification:
        return Notification.model_validate(
            dict(zip(("id", "kind", "title", "body", "url", "created_at"), row, strict=True))
        )

    def pending(self, now: datetime) -> list[Delivery]:
        return self.database.read(
            lambda c: [
                Delivery(
                    notification=self._notification(r[:6]),
                    subscription_id=r[6],
                    subscription=PushSubscription.model_validate_json(r[7]),
                    attempts=r[8],
                )
                for r in c.execute(
                    """SELECT n.id, n.kind, n.title, n.body, n.url, n.created_at,
            s.id, s.payload, d.attempts FROM notification_deliveries d
            JOIN notification_outbox n ON n.id=d.notification_id
            JOIN push_subscriptions s ON s.id=d.subscription_id
            WHERE d.status='pending' AND d.next_attempt_at<=?
            ORDER BY d.next_attempt_at, n.id LIMIT 20""",
                    (timestamp(now),),
                )
            ]
        )

    def result(self, delivery: Delivery, status: str, now: datetime) -> None:
        def write(connection: sqlite3.Connection) -> None:
            if status == "expired":
                connection.execute(
                    "DELETE FROM push_subscriptions WHERE id=?", (delivery.subscription_id,)
                )
                return
            attempts = delivery.attempts + 1
            terminal = status == "sent" or status == "rejected" or attempts >= 4
            state = "sent" if status == "sent" else "failed" if terminal else "pending"
            delay = (1, 5, 15)[min(attempts - 1, 2)]
            connection.execute(
                """UPDATE notification_deliveries SET attempts=?, status=?, next_attempt_at=?
                WHERE notification_id=? AND subscription_id=?""",
                (
                    attempts,
                    state,
                    timestamp(now + timedelta(minutes=delay)),
                    delivery.notification.id,
                    delivery.subscription_id,
                ),
            )

        self.database.write(write)
