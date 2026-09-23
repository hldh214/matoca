"""Deterministic timing policy shared by live evaluation and historical replay."""

from datetime import datetime, timedelta

from pydantic import BaseModel

from matoca_service.prediction.models import Prediction


class TimingDecision(BaseModel):
    evaluated_at: datetime
    checked_at: datetime
    fresh: bool
    official_minutes: int | None
    official_is_more: bool
    prediction: Prediction | None
    arrival_at: datetime
    early_tolerance_minutes: int
    model_error_minutes: int
    reason_code: str
    reason: str
    would_submit: bool = False


def evaluate_timing(
    *,
    evaluated_at: datetime,
    checked_at: datetime,
    arrival_at: datetime,
    official_minutes: int | None,
    official_is_more: bool,
    prediction: Prediction | None,
    early_tolerance_minutes: int,
    model_error_minutes: int,
    available: bool = True,
    observation_fresh: bool = True,
) -> TimingDecision:
    fresh = observation_fresh and timedelta() <= evaluated_at - checked_at <= timedelta(minutes=1)
    if evaluated_at > arrival_at + timedelta(minutes=2):
        code, reason = "expired", "到着予定から2分を過ぎたため、監視を終了しました"
    elif not fresh:
        code, reason = "stale", "最新情報を確認できないため、受付を保留しました"
    elif not available:
        code, reason = "unavailable", "受付状況が変更されました"
    elif evaluated_at >= arrival_at:
        code, reason = "arrival", "到着予定を迎え、受付条件を満たしました"
    elif official_minutes is None or official_minutes < 0 or official_is_more:
        code, reason = (
            "estimate_unavailable",
            "最新の正確な待ち時間を取得できないため、受付を保留しました",
        )
    elif prediction is None:
        code, reason = "prediction_unavailable", "予測を確認できないため、受付を保留しました"
    elif prediction.target == "pre_call" and prediction.effective_samples == 0:
        code, reason = (
            "pre_call_samples_missing",
            "事前呼出の記録がないため、到着予定まで受付を待ちます",
        )
    elif evaluated_at + timedelta(
        minutes=max(0, prediction.fast_minutes - model_error_minutes)
    ) < arrival_at - timedelta(minutes=early_tolerance_minutes):
        code, reason = "too_early", "事前呼出が早まる可能性があるため、次回の評価を待っています"
    else:
        code, reason = "timing_ready", "到着予定に対する受付条件を満たしました"
    return TimingDecision(
        evaluated_at=evaluated_at,
        checked_at=checked_at,
        fresh=fresh,
        official_minutes=official_minutes,
        official_is_more=official_is_more,
        prediction=prediction,
        arrival_at=arrival_at,
        early_tolerance_minutes=early_tolerance_minutes,
        model_error_minutes=model_error_minutes,
        reason_code=code,
        reason=reason,
        would_submit=code in {"arrival", "timing_ready"},
    )
