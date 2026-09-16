import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

from matoca_service.matoca.models import Waiting
from matoca_service.notifications.events import queue_observation
from matoca_service.storage.database import Database
from matoca_service.tracking.models import (
    QueueIntent,
    QueueIntentSummary,
    QueueObservation,
    QueueRead,
    QueueSession,
)


def _minute(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(second=0, microsecond=0)


def _text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _optional_time(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value is not None else None


class QueueRepository:
    INTENT_RECONCILIATION_WINDOW = timedelta(minutes=10)

    def __init__(self, database: Database) -> None:
        self._database = database

    def begin_intent(
        self,
        intent: QueueIntent,
        *,
        on_begin: Callable[[sqlite3.Connection], None] | None = None,
    ) -> None:
        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                """INSERT INTO queue_intents
               (intent_id, merchant_key, shop_id, submitted_at,
                official_minutes_at_submission, official_is_more_at_submission,
                adult_count, child_count, source, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    intent.intent_id,
                    intent.merchant_key,
                    intent.shop_id,
                    _text(intent.submitted_at),
                    intent.official_minutes_at_submission,
                    intent.official_is_more_at_submission,
                    intent.adult_count,
                    intent.child_count,
                    intent.source,
                ),
            )
            if on_begin is not None:
                on_begin(connection)

        self._database.write(write)

    def mark_intent_unresolved(self, intent_id: str, error_code: str) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE queue_intents SET status = 'unresolved', error_code = ?
                   WHERE intent_id = ?""",
                (error_code, intent_id),
            )
        )

    def mark_intent_failed(self, intent_id: str, error_code: str) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE queue_intents SET status = 'failed', error_code = ?
                   WHERE intent_id = ? AND status IN ('pending', 'unresolved')""",
                (error_code, intent_id),
            )
        )

    def resolve_intent(
        self,
        intent_id: str,
        *,
        waiting_id: int,
        number: int | None,
        count: int | None,
        observed_at: datetime,
    ) -> QueueSession:
        def write(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                """SELECT merchant_key, shop_id, submitted_at, official_minutes_at_submission,
                          official_is_more_at_submission, adult_count, child_count, source
                   FROM queue_intents WHERE intent_id = ?""",
                (intent_id,),
            ).fetchone()
            if row is None:
                raise LookupError(intent_id)
            connection.execute(
                """INSERT INTO queue_sessions
                   (intent_id, merchant_key, shop_id, waiting_id, number, adult_count, child_count,
                    source, submitted_at, first_observed_at, official_minutes_at_submission,
                    official_is_more_at_submission, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
                (
                    intent_id,
                    row[0],
                    row[1],
                    waiting_id,
                    number,
                    row[5],
                    row[6],
                    row[7],
                    row[2],
                    _text(_minute(observed_at)),
                    row[3],
                    row[4],
                ),
            )
            connection.execute(
                "UPDATE queue_intents SET status = 'resolved', waiting_id = ? WHERE intent_id = ?",
                (waiting_id, intent_id),
            )
            session_id = connection.execute(
                "SELECT session_id FROM queue_sessions WHERE intent_id = ?", (intent_id,)
            ).fetchone()[0]
            self._save_observation(connection, session_id, observed_at, count)
            if count == 0:
                at = _text(_minute(observed_at))
                connection.execute(
                    """UPDATE queue_sessions SET status='called', called_at=?, terminal_at=?
                       WHERE session_id=?""",
                    (at, at, session_id),
                )

        self._database.write(write)
        return next(item for item in self.list_sessions() if item.intent_id == intent_id)

    def record_waiting(
        self, merchant_key: str, observed_at: datetime, waiting: list[Waiting]
    ) -> None:
        self.record_merchant_read(
            merchant_key,
            observed_at,
            [
                QueueRead(
                    merchant_key=merchant_key,
                    observed_at=observed_at,
                    waiting_id=item.id,
                    shop_id=int(item.shop_id) if item.shop_id is not None else None,
                    number=item.number,
                    count=item.count,
                    adult_count=item.adult_count,
                    child_count=item.child_count,
                )
                for item in waiting
            ],
        )

    def record_merchant_read(
        self, merchant_key: str, observed_at: datetime, reads: list[QueueRead]
    ) -> None:
        def write(connection: sqlite3.Connection) -> None:
            at = _text(_minute(observed_at))
            connection.execute(
                """INSERT INTO queue_tracking_state VALUES (?, ?, ?, NULL)
                   ON CONFLICT (merchant_key) DO UPDATE SET
                   last_attempt_at=excluded.last_attempt_at,
                   last_success_at=excluded.last_success_at, error_code=NULL""",
                (merchant_key, at, at),
            )
            seen = {read.waiting_id for read in reads}
            active = connection.execute(
                """SELECT session_id, waiting_id, cancellation_requested_at FROM queue_sessions
                   WHERE merchant_key=? AND status='active'""",
                (merchant_key,),
            ).fetchall()
            for session_id, waiting_id, cancellation_requested_at in active:
                if waiting_id not in seen:
                    if cancellation_requested_at is not None:
                        connection.execute(
                            """UPDATE queue_sessions SET status='cancelled', cancelled_at=?,
                                      terminal_at=? WHERE session_id=?""",
                            (at, at, session_id),
                        )
                    else:
                        connection.execute(
                            """UPDATE queue_sessions SET status='unknown', terminal_at=?
                               WHERE session_id=?""",
                            (at, session_id),
                        )
            for read in reads:
                self._record_read(connection, read)

        self._database.write(write)

    def record_read(self, read: QueueRead) -> None:
        self.record_merchant_read(read.merchant_key, read.observed_at, [read])

    def _record_read(self, connection: sqlite3.Connection, read: QueueRead) -> None:
        row = connection.execute(
            """SELECT session_id, status, cancellation_requested_at FROM queue_sessions
               WHERE merchant_key=? AND waiting_id=?""",
            (read.merchant_key, read.waiting_id),
        ).fetchone()
        if row is None:
            intent = connection.execute(
                """SELECT intent_id, submitted_at, official_minutes_at_submission,
                          official_is_more_at_submission, source
                   FROM queue_intents
                   WHERE merchant_key=? AND shop_id=? AND adult_count IS ? AND child_count IS ?
                     AND status IN ('pending', 'unresolved')
                     AND submitted_at BETWEEN ? AND ?
                   ORDER BY submitted_at LIMIT 1""",
                (
                    read.merchant_key,
                    read.shop_id,
                    read.adult_count,
                    read.child_count,
                    _text(read.observed_at - self.INTENT_RECONCILIATION_WINDOW),
                    _text(read.observed_at),
                ),
            ).fetchone()
            connection.execute(
                """INSERT INTO queue_sessions
                   (intent_id, merchant_key, shop_id, waiting_id, number, adult_count, child_count,
                    source, submitted_at, first_observed_at, official_minutes_at_submission,
                    official_is_more_at_submission, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
                (
                    intent[0] if intent else None,
                    read.merchant_key,
                    read.shop_id,
                    read.waiting_id,
                    read.number,
                    read.adult_count,
                    read.child_count,
                    intent[4] if intent else "adopted",
                    intent[1] if intent else None,
                    _text(_minute(read.observed_at)),
                    intent[2] if intent else None,
                    intent[3] if intent else None,
                ),
            )
            if intent is not None:
                connection.execute(
                    """UPDATE queue_intents SET status='resolved', waiting_id=?, error_code=NULL
                       WHERE intent_id=?""",
                    (read.waiting_id, intent[0]),
                )
            session_id, status, cancellation_requested_at = cast(
                tuple[int, str, str | None],
                connection.execute(
                    """SELECT session_id, status, cancellation_requested_at FROM queue_sessions
                       WHERE merchant_key=? AND waiting_id=?""",
                    (read.merchant_key, read.waiting_id),
                ).fetchone(),
            )
        else:
            session_id, status, cancellation_requested_at = cast(tuple[int, str, str | None], row)
        if status != "active":
            return
        if cancellation_requested_at is not None:
            if read.observed_at <= datetime.fromisoformat(cancellation_requested_at):
                return
            connection.execute(
                "UPDATE queue_sessions SET cancellation_requested_at=NULL WHERE session_id=?",
                (session_id,),
            )
        self._save_observation(connection, session_id, read.observed_at, read.count)
        if read.count == 0:
            at = _text(_minute(read.observed_at))
            connection.execute(
                """UPDATE queue_sessions SET status='called', called_at=?, terminal_at=?
                   WHERE session_id=?""",
                (at, at, session_id),
            )

    @staticmethod
    def _save_observation(
        connection: sqlite3.Connection, session_id: int, observed_at: datetime, count: int | None
    ) -> None:
        connection.execute(
            """INSERT INTO queue_session_observations VALUES (?, ?, ?)
               ON CONFLICT (session_id, observed_minute) DO UPDATE SET count=excluded.count""",
            (session_id, _text(_minute(observed_at)), count),
        )
        queue_observation(connection, session_id, _minute(observed_at), count)

    def record_failure(self, merchant_key: str, observed_at: datetime, error_code: str) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """INSERT INTO queue_tracking_state VALUES (?, ?, NULL, ?)
               ON CONFLICT (merchant_key) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,
               error_code=excluded.error_code""",
                (merchant_key, _text(_minute(observed_at)), error_code),
            )
        )

    def mark_cancelled(self, merchant_key: str, waiting_id: int, cancelled_at: datetime) -> None:
        at = _text(cancelled_at)
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE queue_sessions SET status='cancelled', called_at=NULL,
                          cancelled_at=?, terminal_at=?
               WHERE merchant_key=? AND waiting_id=? AND status IN ('active', 'called')""",
                (at, at, merchant_key, waiting_id),
            )
        )

    def mark_cancellation_requested(
        self, merchant_key: str, waiting_id: int, requested_at: datetime
    ) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE queue_sessions SET cancellation_requested_at=?
                   WHERE merchant_key=? AND waiting_id=? AND status='active'""",
                (_text(requested_at), merchant_key, waiting_id),
            )
        )

    def clear_cancellation_requested(self, merchant_key: str, waiting_id: int) -> None:
        self._database.write(
            lambda connection: connection.execute(
                """UPDATE queue_sessions SET cancellation_requested_at=NULL
                   WHERE merchant_key=? AND waiting_id=? AND status='active'""",
                (merchant_key, waiting_id),
            )
        )

    def active_sessions(self) -> list[QueueSession]:
        return [item for item in self.list_sessions() if item.status == "active"]

    def list_unfinished_intents(self) -> list[QueueIntentSummary]:
        def read(connection: sqlite3.Connection) -> list[QueueIntentSummary]:
            rows = connection.execute(
                """SELECT intent_id, merchant_key, shop_id, submitted_at,
                          official_minutes_at_submission, official_is_more_at_submission,
                          adult_count, child_count, source, status, error_code
                   FROM queue_intents WHERE status IN ('pending', 'unresolved')
                   ORDER BY submitted_at DESC"""
            ).fetchall()
            return [
                QueueIntentSummary(
                    intent_id=row[0],
                    merchant_key=row[1],
                    shop_id=row[2],
                    submitted_at=datetime.fromisoformat(row[3]),
                    official_minutes_at_submission=row[4],
                    official_is_more_at_submission=bool(row[5]) if row[5] is not None else None,
                    adult_count=row[6],
                    child_count=row[7],
                    source=row[8],
                    status=row[9],
                    error_code=row[10],
                )
                for row in rows
            ]

        return self._database.read(read)

    def list_sessions(self) -> list[QueueSession]:
        def read(connection: sqlite3.Connection) -> list[QueueSession]:
            rows = connection.execute(
                """SELECT q.*, t.error_code FROM queue_sessions q
                   LEFT JOIN queue_tracking_state t ON t.merchant_key=q.merchant_key
                   ORDER BY COALESCE(q.submitted_at, q.first_observed_at) DESC"""
            ).fetchall()
            result = []
            for row in rows:
                observations = connection.execute(
                    """SELECT observed_minute, count FROM queue_session_observations
                       WHERE session_id=? ORDER BY observed_minute""",
                    (row[0],),
                ).fetchall()
                result.append(
                    QueueSession(
                        session_id=row[0],
                        intent_id=row[1],
                        merchant_key=row[2],
                        shop_id=row[3],
                        waiting_id=row[4],
                        number=row[5],
                        adult_count=row[6],
                        child_count=row[7],
                        source=row[8],
                        submitted_at=_optional_time(row[9]),
                        first_observed_at=datetime.fromisoformat(row[10]),
                        official_minutes_at_submission=row[11],
                        official_is_more_at_submission=(
                            bool(row[12]) if row[12] is not None else None
                        ),
                        called_at=_optional_time(row[13]),
                        cancelled_at=_optional_time(row[14]),
                        status=row[16],
                        stale=row[18] is not None,
                        error_code=row[18],
                        observations=[
                            QueueObservation(observed_at=datetime.fromisoformat(o[0]), count=o[1])
                            for o in observations
                        ],
                    )
                )
            return result

        return self._database.read(read)
