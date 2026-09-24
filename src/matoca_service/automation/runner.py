import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

from matoca_service.automation.decisions import TimingDecision, evaluate_official_timing
from matoca_service.automation.models import (
    AutomationEditContext,
    AutomationEditRequest,
    AutomationRequest,
    AutomationTask,
    TaskState,
)
from matoca_service.automation.repository import AutomationRepository, TaskConflictError
from matoca_service.matoca.models import Shop, ShopForms
from matoca_service.storage.asyncio import run_storage

if TYPE_CHECKING:
    from matoca_service.service import MatocaService


def form_signature(shop: Shop) -> str:
    if shop.forms is None:
        raise TaskConflictError("受付に必要な情報を取得できませんでした")
    return json.dumps(shop.forms.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


def form_revision(shop: Shop) -> str:
    """Bind the visible choices and limits, excluding defaults and decoration."""
    if shop.forms is None:
        raise TaskConflictError("受付に必要な情報を取得できませんでした")
    forms = shop.forms.model_dump()
    visible = {
        key: forms.get(key)
        for key in (
            "min_adult",
            "max_adult",
            "min_child",
            "max_child",
            "is_ticketing_only",
            "is_confirm_tel",
            "is_confirm_child",
        )
    }
    items = forms.get("confirm_items") or []
    visible["confirm_items"] = (
        [
            {
                "enable": item.get("enable"),
                "title": item.get("title"),
                "sub_items": [
                    {
                        key: option.get(key)
                        for key in ("enable", "disabled", "sub_item_index", "text")
                    }
                    if isinstance(option, dict)
                    else option
                    for option in item.get("sub_items", [])
                ],
            }
            if isinstance(item, dict) and isinstance(item.get("sub_items"), list)
            else item
            for item in items
        ]
        if isinstance(items, list)
        else items
    )
    return sha256(json.dumps(visible, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def form_matches(
    task: AutomationRequest, shop: Shop, *, saved_signature: str | None = None
) -> bool:
    """Compare selected meanings, including signatures stored by older releases."""
    if shop.forms is None:
        return False

    def unsupported(value: object, *, input_context: bool = False) -> bool:
        if isinstance(value, list):
            return any(unsupported(item, input_context=input_context) for item in value)
        if isinstance(value, dict):
            return (
                value.get("required") is True
                or (input_context and value.get("enable") is True)
                or any(unknown_input(key, item) for key, item in value.items())
            )
        return False

    def unknown_input(key: str, value: object) -> bool:
        return (key.startswith(("is_confirm_", "is_required_")) and value is True) or unsupported(
            value, input_context=any(word in key for word in ("input", "field", "confirm"))
        )

    def check_extras(value: dict[str, object], known: set[str]) -> None:
        if any(unknown_input(key, item) for key, item in value.items() if key not in known):
            raise ValueError("unsupported required input")

    def semantics(form: dict[str, object]) -> object:
        items = form.get("confirm_items") or []
        if not isinstance(items, list):
            raise ValueError("invalid confirmation")
        selected = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or type(item.get("enable")) is not bool:
                raise ValueError("invalid confirmation")
            if not item["enable"]:
                continue
            if index > 1:
                raise ValueError("unsupported confirmation")
            check_extras(item, {"enable", "title", "sub_items"})
            answer = task.answer1 if index == 0 else task.answer2
            options = item.get("sub_items")
            if not isinstance(options, list):
                raise ValueError("invalid options")
            for entry in options:
                if (
                    isinstance(entry, dict)
                    and entry.get("enable") is True
                    and not entry.get("disabled")
                ):
                    check_extras(entry, {"enable", "disabled", "sub_item_index", "text"})
            option = next(
                (
                    entry
                    for entry in options
                    if isinstance(entry, dict)
                    and entry.get("sub_item_index") == answer
                    and entry.get("enable") is True
                    and not entry.get("disabled")
                ),
                None,
            )
            if option is None:
                raise ValueError("selected option removed")
            selected.append((index, item.get("title"), answer, option.get("text")))
        known = set(ShopForms.model_fields) | {"confirm_items"}
        check_extras(form, known)
        return (
            form.get("is_ticketing_only", False),
            form.get("is_confirm_tel", False),
            form.get("is_confirm_child", False),
            selected,
        )

    try:
        signature = (
            saved_signature
            if saved_signature is not None
            else (task.form_signature if isinstance(task, AutomationTask) else form_signature(shop))
        )
        return semantics(json.loads(signature)) == semantics(shop.forms.model_dump())
    except ValueError, TypeError:
        return False


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

    def _validate_request(self, request: AutomationRequest, shop: Shop) -> None:
        from matoca_service.service import (
            QueueSubmission,
            QueueUnavailableError,
            validate_queue_submission,
        )

        if request.arrival_at <= self.now():
            raise QueueUnavailableError("到着予定は未来の日時を指定してください")
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

    async def create(self, request: AutomationRequest) -> AutomationTask:
        shop = await self.service.shop_detail(request.merchant_key, request.shop_id)
        if request.form_revision is not None and request.form_revision != form_revision(shop):
            raise TaskConflictError(
                "受付の質問が変わりました。開き直して選択内容を確認してください"
            )
        self._validate_request(request, shop)
        task = await run_storage(
            self.repository.create,
            request,
            shop.sub_name or shop.name,
            form_signature(shop),
            self.now(),
        )
        self.service.collection_coordinator.wake(request.merchant_key)
        return task

    @staticmethod
    def _editable(task: AutomationTask, expected_version: int | None = None) -> None:
        if (
            task.timing_policy != "official"
            or task.state not in {"scheduled", "monitoring", "needs_attention"}
            or task.intent_id is not None
            or (expected_version is not None and task.version != expected_version)
        ):
            raise TaskConflictError("状態が変わりました。入力を保持して最新情報を確認してください")

    async def _edit_shop(self, task: AutomationTask) -> Shop:
        lat, lng = await run_storage(
            self.service._stored_shop_coordinates, task.merchant_key, task.shop_id
        )
        return await self.service._authenticated_read(
            task.merchant_key, lambda client: client.get_shop(task.shop_id, lat=lat, lng=lng)
        )

    async def edit_context(self, task_id: str) -> AutomationEditContext:
        async with self.service._operation_lock:
            task = await run_storage(self.repository.get, task_id)
            self._editable(task)
            shop = await self._edit_shop(task)
            task = await run_storage(self.repository.get, task_id)
            self._editable(task)
            return AutomationEditContext(
                task=task,
                shop=shop,
                selections_compatible=form_matches(task, shop),
                form_revision=form_revision(shop),
            )

    async def edit(self, task_id: str, edit: AutomationEditRequest) -> AutomationTask:
        async with self.service._operation_lock:
            task = await run_storage(self.repository.get, task_id)
            self._editable(task, edit.expected_version)
            shop = await self._edit_shop(task)
            signature = form_signature(shop)
            if form_revision(shop) != edit.form_revision:
                raise TaskConflictError(
                    "受付の質問が変わりました。最新情報を読み込み、選択を確認してください"
                )
            request = AutomationRequest(
                merchant_key=task.merchant_key,
                shop_id=task.shop_id,
                mode=task.mode,
                **edit.model_dump(exclude={"expected_version", "form_revision"}),
            )
            task = await run_storage(self.repository.get, task_id)
            self._editable(task, edit.expected_version)
            self._validate_request(request, shop)
            if not form_matches(request, shop, saved_signature=signature):
                raise TaskConflictError(
                    "対応できない確認項目があります。店舗の受付内容を確認してください"
                )
            updated = await run_storage(self.repository.edit, task, request, signature, self.now())
        self.service.collection_coordinator.wake(task.merchant_key)
        return updated

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
            if task.timing_policy == "official" and task.state in {
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

    async def _record_blocked(
        self, task: AutomationTask, reason_code: str, reason: str, checked_at: datetime
    ) -> None:
        evidence = evaluate_official_timing(
            evaluated_at=self.now(),
            checked_at=checked_at,
            arrival_at=task.arrival_at,
            official_minutes=None,
            official_is_more=False,
            available=False,
            observation_fresh=reason_code not in {"read_error", "expired"},
        ).model_copy(update={"reason_code": reason_code, "reason": reason, "would_submit": False})
        await run_storage(self.repository.record_decision, task.id, evidence)

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
            ReceptionUnavailableError,
        )

        if task.state in {"cancelled", "expired", "failed", "completed", "unknown"}:
            return
        if task.intent_id:
            await self._recover(task)
            return
        if self.now() > task.arrival_at + timedelta(minutes=2):
            await self._record_blocked(task, "expired", "到着予定から2分を過ぎました", self.now())
            await self._record(
                task, "expired", "到着予定から2分を過ぎたため、自動受付を終了しました"
            )
            return
        if task.state not in {"scheduled", "monitoring"}:
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
        evidence: TimingDecision | None = None

        async def check(shop: Shop) -> None:
            nonlocal evidence
            if not form_matches(task, shop):
                raise DeferredDecision(
                    "needs_attention", "受付フォームが変更されました。選択内容の確認が必要です"
                )
            estimate = shop.waiting_time
            evidence = evaluate_official_timing(
                evaluated_at=self.now(),
                checked_at=checks_started,
                arrival_at=task.arrival_at,
                official_minutes=estimate.minutes if estimate else None,
                official_is_more=estimate.is_more if estimate else False,
            )
            await run_storage(self.repository.record_decision, task.id, evidence)
            if not evidence.would_submit:
                raise DeferredDecision(
                    "expired" if evidence.reason_code == "expired" else "monitoring",
                    evidence.reason,
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
            if task.mode == "simulation":
                from matoca_service.service import validate_queue_submission

                # Only authenticated reads: never enter the real submission/intents path.
                if await run_storage(self.service._queues.list_unfinished_intents):
                    raise QueueUnavailableError(
                        "受付結果が不明な申込があります。現在の受付を確認してください"
                    )
                for key in self.service._registry.merchants:
                    waiting = await self.service._authenticated_read(
                        key, lambda client: client.list_waiting()
                    )
                    if waiting:
                        raise QueueUnavailableError("すでに受付中の順番待ちがあります")
                lat, lng = await run_storage(
                    self.service._stored_shop_coordinates, task.merchant_key, task.shop_id
                )
                shop = await self.service._authenticated_read(
                    task.merchant_key,
                    lambda client: client.get_shop(task.shop_id, lat=lat, lng=lng),
                )
                validate_queue_submission(shop, submission, [])
                await check(shop)
                authorize_send()
                await self._record(
                    task,
                    "simulated",
                    "シミュレーション: 受付条件を満たしました。実際の申込は行っていません",
                )
                return
            await self.service._create_waiting_unlocked(
                task.merchant_key,
                submission,
                before_send=check,
                authorize_send=authorize_send,
                persist_intent=lambda intent: self.repository.begin_submission(task, intent),
                source="automation",
            )
        except DeferredDecision as decision:
            if evidence is None or evidence.would_submit:
                await self._record_blocked(task, decision.state, decision.decision, checks_started)
            current = await run_storage(self.repository.get, task.id)
            await self._record(current, decision.state, decision.decision)
        except QueueOutcomeUnknownError:
            current = await run_storage(self.repository.get, task.id)
            await self._record(current, "reconciling", "受付結果を照合中です。自動で再送しません")
        except (QueueUnavailableError, TaskConflictError) as error:
            unavailable = isinstance(error, ReceptionUnavailableError)
            reason = f"{error}。次回の評価で再確認します" if unavailable else str(error)
            reason_code = (
                error.reason_code
                if isinstance(error, ReceptionUnavailableError)
                else "checks_failed"
            )
            await self._record_blocked(task, reason_code, reason, checks_started)
            current = await run_storage(self.repository.get, task.id)
            if current.intent_id:
                await self._recover(current)
            else:
                state: TaskState = (
                    "expired"
                    if self.now() > task.arrival_at + timedelta(minutes=2)
                    else "monitoring"
                    if unavailable
                    else "needs_attention"
                )
                await self._record(current, state, reason)
        except Exception:
            await self._record_blocked(
                task, "read_error", "最新情報を確認できません", checks_started
            )
            current = await run_storage(self.repository.get, task.id)
            await self._record(
                current,
                "reconciling"
                if current.intent_id
                else "expired"
                if self.now() > task.arrival_at + timedelta(minutes=2)
                else "monitoring",
                "最新情報を確認できません。送信せずに受付状況を確認します",
            )
        else:
            current = await run_storage(self.repository.get, task.id)
            # The response is already persisted; recovery also handles immediately called tickets.
            await self._recover(current)
