import json
import sqlite3
import uuid
from datetime import UTC, datetime

from matoca_service.automation.decisions import TimingDecision
from matoca_service.automation.models import (
    AutomationEvent,
    AutomationRequest,
    AutomationTask,
    TaskState,
)
from matoca_service.notifications.events import task_event
from matoca_service.storage.database import Database
from matoca_service.tracking.models import QueueIntent
from matoca_service.tracking.repository import QueueRepository


class TaskConflictError(RuntimeError):
    pass


class AutomationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(
        self, request: AutomationRequest, shop_name: str, form_signature: str, now: datetime
    ) -> AutomationTask:
        task = AutomationTask(
            **request.model_dump(),
            id=str(uuid.uuid4()),
            shop_name=shop_name,
            form_signature=form_signature,
            created_at=now,
            next_evaluation_at=now,
        )

        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                """INSERT INTO automation_tasks
                (id, merchant_key, payload, form_signature, state, version,
                 next_evaluation_at, last_decision)
                VALUES (?, ?, ?, ?, 'scheduled', 0, ?, ?)""",
                (
                    task.id,
                    task.merchant_key,
                    task.model_dump_json(),
                    form_signature,
                    now.isoformat(),
                    task.last_decision,
                ),
            )
            self._event(connection, task.id, now, task.state, task.last_decision)

        self._database.write(write)
        return task

    @staticmethod
    def _event(
        connection: sqlite3.Connection, task_id: str, now: datetime, state: TaskState, decision: str
    ) -> None:
        connection.execute(
            "INSERT INTO automation_events (task_id, at, state, decision) VALUES (?, ?, ?, ?)",
            (task_id, now.astimezone(UTC).isoformat(), state, decision),
        )
        row = connection.execute(
            "SELECT payload FROM automation_tasks WHERE id=?", (task_id,)
        ).fetchone()
        if json.loads(row[0]).get("mode", "live") == "live":
            task_event(connection, task_id, state, now)

    def list_tasks(self) -> list[AutomationTask]:
        def read(connection: sqlite3.Connection) -> list[AutomationTask]:
            rows = connection.execute("""SELECT payload, form_signature, state, version, intent_id,
                last_decision, evaluated_at, next_evaluation_at
                FROM automation_tasks ORDER BY rowid DESC""").fetchall()
            return [
                AutomationTask.model_validate(
                    {
                        **json.loads(row[0]),
                        "form_signature": row[1],
                        "state": row[2],
                        "version": row[3],
                        "intent_id": row[4],
                        "last_decision": row[5],
                        "evaluated_at": row[6],
                        "next_evaluation_at": row[7],
                    }
                )
                for row in rows
            ]

        return self._database.read(read)

    def get(self, task_id: str) -> AutomationTask:
        for task in self.list_tasks():
            if task.id == task_id:
                return task
        raise LookupError(task_id)

    def has_active_task(self, merchant_key: str) -> bool:
        return self._database.read(
            lambda connection: (
                connection.execute(
                    """SELECT 1 FROM automation_tasks WHERE merchant_key=?
            AND state IN ('scheduled','monitoring','submitting','reconciling','queued') LIMIT 1""",
                    (merchant_key,),
                ).fetchone()
                is not None
            )
        )

    def _transition(
        self,
        connection: sqlite3.Connection,
        task: AutomationTask,
        state: TaskState,
        decision: str,
        now: datetime,
        next_at: datetime | None = None,
        intent_id: str | None = None,
    ) -> None:
        cursor = connection.execute(
            """UPDATE automation_tasks SET state=?, version=version+1,
            last_decision=?, evaluated_at=?, next_evaluation_at=?, intent_id=COALESCE(?, intent_id)
            WHERE id=? AND version=? AND state=?""",
            (
                state,
                decision,
                now.isoformat(),
                next_at.isoformat() if next_at else None,
                intent_id,
                task.id,
                task.version,
                task.state,
            ),
        )
        if cursor.rowcount != 1:
            raise TaskConflictError("状態が変わりました。最新情報を確認してください")
        self._event(connection, task.id, now, state, decision)

    def transition(
        self,
        task: AutomationTask,
        state: TaskState,
        decision: str,
        now: datetime,
        next_at: datetime | None = None,
    ) -> AutomationTask:
        self._database.write(
            lambda connection: self._transition(connection, task, state, decision, now, next_at)
        )
        return self.get(task.id)

    def edit(
        self, task: AutomationTask, request: AutomationRequest, signature: str, now: datetime
    ) -> AutomationTask:
        if (request.merchant_key, request.shop_id, request.mode) != (
            task.merchant_key,
            task.shop_id,
            task.mode,
        ):
            raise TaskConflictError("加盟店・店舗・実行方法は変更できません")
        decision = "設定を更新しました。新しい内容で再評価します"
        payload = task.model_copy(update=request.model_dump()).model_dump_json()

        def write(connection: sqlite3.Connection) -> None:
            cursor = connection.execute(
                """UPDATE automation_tasks SET payload=?, form_signature=?, state='scheduled',
                version=version+1, last_decision=?, next_evaluation_at=?
                WHERE id=? AND version=? AND intent_id IS NULL
                AND state IN ('scheduled', 'monitoring', 'needs_attention')""",
                (payload, signature, decision, now.isoformat(), task.id, task.version),
            )
            if cursor.rowcount != 1:
                raise TaskConflictError(
                    "状態が変わりました。入力を保持して最新情報を確認してください"
                )
            self._event(connection, task.id, now, "scheduled", decision)

        self._database.write(write)
        return self.get(task.id)

    def begin_submission(self, task: AutomationTask, intent: QueueIntent) -> None:
        if task.mode != "live" or task.state not in {"scheduled", "monitoring"}:
            raise TaskConflictError("この自動受付は送信できません")
        QueueRepository(self._database).begin_intent(
            intent,
            on_begin=lambda connection: self._transition(
                connection,
                task,
                "submitting",
                "受付を送信しています",
                intent.submitted_at,
                intent_id=intent.intent_id,
            ),
        )

    def record_decision(self, task_id: str, decision: TimingDecision) -> None:
        self._database.write(
            lambda connection: connection.execute(
                "INSERT INTO automation_decisions (task_id, evaluated_at, payload) "
                "VALUES (?, ?, ?)",
                (task_id, decision.evaluated_at.isoformat(), decision.model_dump_json()),
            )
        )

    def list_decisions(self, task_id: str) -> list[TimingDecision]:
        self.get(task_id)
        return self._database.read(
            lambda connection: [
                TimingDecision.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload FROM automation_decisions WHERE task_id=? ORDER BY id",
                    (task_id,),
                )
            ]
        )

    def intent_status(self, intent_id: str) -> str | None:
        row = self._database.read(
            lambda connection: connection.execute(
                "SELECT status FROM queue_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
        )
        return str(row[0]) if row else None

    def resolve_absent(self, task: AutomationTask, now: datetime) -> AutomationTask:
        def write(connection: sqlite3.Connection) -> None:
            if task.intent_id:
                cursor = connection.execute(
                    """UPDATE queue_intents SET status='failed',
                    error_code='user_confirmed_absent'
                    WHERE intent_id=? AND status IN ('pending','unresolved')""",
                    (task.intent_id,),
                )
                if cursor.rowcount != 1:
                    raise TaskConflictError("受付結果が変わりました。最新情報を確認してください")
            self._transition(
                connection, task, "cancelled", "受付がないことを確認し、監視を終了しました", now
            )

        self._database.write(write)
        return self.get(task.id)

    def events(self, after_id: int = 0) -> list[AutomationEvent]:
        return self._database.read(
            lambda connection: [
                AutomationEvent(id=row[0], task_id=row[1], at=row[2], state=row[3], decision=row[4])
                for row in connection.execute(
                    "SELECT id, task_id, at, state, decision FROM automation_events "
                    "WHERE id>? ORDER BY id",
                    (after_id,),
                )
            ]
        )
