import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from matoca_service.automation.models import AutomationRequest, AutomationTask, TaskState
from matoca_service.automation.repository import AutomationRepository, TaskConflictError
from matoca_service.matoca.models import Shop
from matoca_service.storage.asyncio import run_storage

if TYPE_CHECKING:
    from matoca_service.service import MatocaService


def form_signature(shop: Shop) -> str:
    if shop.forms is None:
        raise TaskConflictError("受付に必要な情報を取得できませんでした")
    return json.dumps(shop.forms.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


class DeferredDecision(Exception):
    def __init__(self, state: TaskState, decision: str) -> None:
        self.state = state
        self.decision = decision


class AutomationRunner:
    def __init__(
        self,
        service: MatocaService,
        repository: AutomationRepository,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.service = service
        self.repository = repository
        self.now = now

    async def create(self, request: AutomationRequest) -> AutomationTask:
        from matoca_service.service import (
            QueueSubmission,
            QueueUnavailableError,
            validate_queue_submission,
        )

        if request.arrival_at <= self.now():
            raise QueueUnavailableError("到着予定は未来の日時を指定してください")
        shop = await self.service.shop_detail(request.merchant_key, request.shop_id)
        submission = QueueSubmission.model_validate(
            request.model_dump(
                include={
                    "shop_id",
                    "adult_count",
                    "child_count",
                    "answer1",
                    "answer2",
                    "in_advance_information",
                }
            )
        )
        # Future tasks can be enabled before reception opens; use the live form.
        validate_queue_submission(
            shop.model_copy(
                update={
                    "is_open": True,
                    "is_issuable": True,
                    "is_holiday": False,
                    "is_suspended": False,
                }
            ),
            submission,
            [],
        )
        if request.arrival_at <= self.now():
            raise QueueUnavailableError("到着予定は未来の日時を指定してください")
        task = await run_storage(
            self.repository.create,
            request,
            shop.sub_name or shop.name,
            form_signature(shop),
            self.now(),
        )
        self.service.collection_coordinator.wake(request.merchant_key)
        return task

    async def cancel(self, task_id: str) -> AutomationTask:
        async with self.service._operation_lock:
            task = await run_storage(self.repository.get, task_id)
            if task.state not in {"scheduled", "monitoring", "needs_attention"} or task.intent_id:
                raise TaskConflictError(
                    "送信開始後は監視を取り消せません。現在の受付を確認してください"
                )
            return await run_storage(
                self.repository.transition, task, "cancelled", "監視を取り消しました", self.now()
            )

    async def resolve(self, task_id: str) -> AutomationTask:
        async with self.service._operation_lock:
            task = await run_storage(self.repository.get, task_id)
            if (
                task.state not in {"needs_attention", "reconciling", "submitting"}
                or task.intent_id is None
            ):
                raise TaskConflictError("確認待ちの受付がありません")
            await self.service._check_account_waiting_unlocked()
            task = await run_storage(self.repository.get, task_id)
            return await run_storage(self.repository.resolve_absent, task, self.now())

    async def run_once(self) -> None:
        tasks = await run_storage(self.repository.list_tasks)
        for task in reversed(tasks):
            if task.state in {
                "scheduled",
                "monitoring",
                "submitting",
                "reconciling",
                "queued",
                "needs_attention",
            }:
                async with self.service._operation_lock:
                    current = await run_storage(self.repository.get, task.id)
                    await self._evaluate(current)

    async def _record(self, task: AutomationTask, state: TaskState, decision: str) -> None:
        await run_storage(
            self.repository.transition,
            task,
            state,
            decision,
            self.now(),
            self.now() + timedelta(minutes=1)
            if state in {"monitoring", "reconciling", "queued"}
            or (state == "needs_attention" and task.intent_id is not None)
            else None,
        )

    async def _recover(self, task: AutomationTask) -> None:
        assert task.intent_id is not None
        try:
            waiting = await self.service._authenticated_read(
                task.merchant_key, lambda client: client.list_waiting()
            )
            await run_storage(
                self.service._queues.record_waiting, task.merchant_key, self.now(), waiting
            )
        except Exception:
            await self._record(
                task,
                "needs_attention",
                "受付結果を確認できません。再申込せず、受付状況を確認してください",
            )
            return
        sessions = await run_storage(self.service._queues.list_sessions)
        session = next((item for item in sessions if item.intent_id == task.intent_id), None)
        if session is not None:
            state: TaskState = {
                "active": "queued",
                "called": "completed",
                "cancelled": "cancelled",
                "unknown": "unknown",
            }[session.status]  # type: ignore[assignment]
            decisions = {
                "queued": "受付済みです。現在の順番待ちを確認してください",
                "completed": "お呼び出しを確認しました",
                "cancelled": "順番待ちが取り消されました",
                "unknown": "順番待ちが見つかりません。結果を確認してください",
            }
            await self._record(task, state, decisions[state])
        else:
            status = await run_storage(self.repository.intent_status, task.intent_id)
            await self._record(
                task,
                "failed" if status == "failed" else "needs_attention",
                "受付が受理されませんでした"
                if status == "failed"
                else "受付結果が不明です。再送しません。受付がないことを確認して監視を終了できます",
            )

    async def _evaluate(self, task: AutomationTask) -> None:
        from matoca_service.service import (
            QueueOutcomeUnknownError,
            QueueSubmission,
            QueueUnavailableError,
        )

        if task.state in {"cancelled", "expired", "failed", "completed", "unknown"}:
            return
        if task.intent_id:
            await self._recover(task)
            return
        if task.state not in {"scheduled", "monitoring"}:
            return
        if self.now() > task.arrival_at + timedelta(minutes=2):
            await self._record(
                task, "expired", "到着予定から2分を過ぎたため、自動受付を終了しました"
            )
            return
        submission = QueueSubmission.model_validate(
            task.model_dump(
                include={
                    "shop_id",
                    "adult_count",
                    "child_count",
                    "answer1",
                    "answer2",
                    "in_advance_information",
                }
            )
        )

        checks_started = self.now()

        async def check(shop: Shop) -> None:
            now = self.now()
            if now > task.arrival_at + timedelta(minutes=2):
                raise DeferredDecision(
                    "expired", "到着予定から2分を過ぎたため、自動受付を終了しました"
                )
            if form_signature(shop) != task.form_signature:
                raise DeferredDecision(
                    "needs_attention", "受付フォームが変更されました。選択内容の確認が必要です"
                )
            estimate = shop.waiting_time
            if estimate is None or estimate.is_more or estimate.minutes < 0:
                raise DeferredDecision(
                    "expired" if now >= task.arrival_at else "monitoring",
                    "最新の正確な待ち時間を取得できないため、受付を保留しました",
                )
            prediction = await self.service.predict(
                task.merchant_key, task.shop_id, estimate.minutes, now
            )
            now = self.now()
            if now > task.arrival_at + timedelta(minutes=2):
                raise DeferredDecision(
                    "expired", "到着予定から2分を過ぎたため、自動受付を終了しました"
                )
            if prediction is None:
                raise DeferredDecision(
                    "expired" if now >= task.arrival_at else "monitoring",
                    "予測を確認できないため、受付を保留しました",
                )
            if now - checks_started > timedelta(minutes=1):
                raise DeferredDecision(
                    "expired" if now >= task.arrival_at else "monitoring",
                    "直前の確認から1分を過ぎたため、最新情報を再確認します",
                )
            if now + timedelta(
                minutes=max(0, prediction.fast_minutes - task.model_error_minutes)
            ) < task.arrival_at - timedelta(minutes=task.early_tolerance_minutes):
                raise DeferredDecision(
                    "monitoring", "早く呼ばれる可能性があるため、次回の評価を待っています"
                )

        def authorize_send() -> None:
            if self.now() > task.arrival_at + timedelta(minutes=2):
                raise DeferredDecision(
                    "expired", "到着予定から2分を過ぎたため、自動受付を終了しました"
                )
            if self.now() - checks_started > timedelta(minutes=1):
                raise DeferredDecision(
                    "failed", "直前の確認から1分を過ぎたため、送信せずに監視を終了しました"
                )

        try:
            await self.service._create_waiting_unlocked(
                task.merchant_key,
                submission,
                before_send=check,
                authorize_send=authorize_send,
                persist_intent=lambda intent: self.repository.begin_submission(task, intent),
                source="automation",
            )
        except DeferredDecision as decision:
            current = await run_storage(self.repository.get, task.id)
            await self._record(current, decision.state, decision.decision)
        except QueueOutcomeUnknownError:
            current = await run_storage(self.repository.get, task.id)
            await self._record(current, "reconciling", "受付結果を照合中です。自動で再送しません")
        except (QueueUnavailableError, TaskConflictError) as error:
            current = await run_storage(self.repository.get, task.id)
            if current.intent_id:
                await self._recover(current)
            else:
                unavailable = str(error) == "受付状況が変更されました"
                state: TaskState = (
                    "expired"
                    if self.now() >= task.arrival_at
                    else "monitoring"
                    if unavailable
                    else "needs_attention"
                )
                await self._record(current, state, str(error))
        except Exception:
            current = await run_storage(self.repository.get, task.id)
            await self._record(
                current,
                "reconciling"
                if current.intent_id
                else "expired"
                if self.now() >= task.arrival_at
                else "monitoring",
                "最新情報を確認できません。送信せずに受付状況を確認します",
            )
        else:
            current = await run_storage(self.repository.get, task.id)
            # The response is already persisted; recovery also handles immediately called tickets.
            await self._recover(current)
