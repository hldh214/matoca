"""Read-only timing replay; historical account/form checks cannot be reconstructed."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from matoca_service.analytics.repository import AnalyticsRepository
from matoca_service.automation.decisions import TimingDecision, evaluate_timing
from matoca_service.prediction.repository import PredictionRepository, PredictionService
from matoca_service.prediction.trends import TrendSummary
from matoca_service.storage.database import Database

TOKYO = ZoneInfo("Asia/Tokyo")


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    merchant_key: str
    shop_id: int = Field(ge=1)
    day: date
    arrival_at: datetime
    early_tolerance_minutes: int = Field(default=15, ge=0, le=120)
    model_error_minutes: int = Field(default=15, ge=0, le=120)

    @field_validator("arrival_at")
    @classmethod
    def aware_arrival(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("到着予定には時差を含めてください")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def same_day(self) -> ReplayRequest:
        if self.arrival_at.astimezone(TOKYO).date() != self.day:
            raise ValueError("到着予定は指定した日本時間の日付に含めてください")
        return self


class ReplayComparison(BaseModel):
    evaluated_at: datetime
    baseline_margin_minutes: int
    suggested_addition_minutes: int | None
    suggested_margin_minutes: int | None
    fixed_would_submit: bool
    suggested_would_submit: bool | None
    trend: TrendSummary


class ReplayResult(BaseModel):
    request: ReplayRequest
    timing_only: bool = True
    limitations: str = (
        "当時のフォームとアカウントの受付状況は不明です。時刻条件のみの再現であり、"
        "実際の受付成功・呼び出し精度を示しません。記録のない時刻は評価しません。"
    )
    decisions: list[TimingDecision]
    first_would_submit_at: datetime | None
    comparison: list[ReplayComparison] = Field(default_factory=list)
    first_suggested_would_submit_at: datetime | None = None


def replay(database: Database, request: ReplayRequest, *, as_of: datetime) -> ReplayResult:
    start = datetime.combine(request.day, time.min, TOKYO).astimezone(UTC)
    end = min(
        datetime.combine(request.day, time.max, TOKYO).astimezone(UTC),
        request.arrival_at + timedelta(minutes=2),
        as_of.astimezone(UTC),
    )
    rows = database.read(
        lambda connection: connection.execute(
            """SELECT observed_minute, waiting_minutes, waiting_is_more,
        list_fresh, detail_fresh, is_open, is_issuable, is_holiday, is_suspended, error_code
        FROM shop_observations WHERE merchant_key=? AND shop_id=?
        AND observed_minute>=? AND observed_minute<=? ORDER BY observed_minute""",
            (request.merchant_key, request.shop_id, start.isoformat(), end.isoformat()),
        ).fetchall()
    )
    predictor = PredictionService(PredictionRepository(database))
    trends = AnalyticsRepository(database).trend_timeline(request.merchant_key, start, end)
    decisions = []
    comparisons = []
    for row in rows:
        observed = datetime.fromisoformat(row[0])
        fresh = bool(row[3] and row[4] and row[9] is None)
        prediction = (
            predictor.predict(request.merchant_key, request.shop_id, row[1], observed)
            if fresh and row[1] is not None and not row[2]
            else None
        )
        decisions.append(
            evaluate_timing(
                evaluated_at=observed,
                checked_at=observed,
                arrival_at=request.arrival_at,
                official_minutes=row[1],
                official_is_more=bool(row[2]),
                prediction=prediction,
                early_tolerance_minutes=request.early_tolerance_minutes,
                model_error_minutes=request.model_error_minutes,
                available=bool(row[5] and row[6] and not row[7] and not row[8]),
                observation_fresh=fresh,
            )
        )
        trend = trends.summary(request.shop_id, observed)
        addition = trend.suggested_addition_minutes
        margin = min(120, request.model_error_minutes + addition) if addition is not None else None
        suggested = (
            evaluate_timing(
                **{
                    **decisions[-1].model_dump(
                        exclude={"reason", "reason_code", "would_submit", "fresh"}
                    ),
                    "prediction": prediction,
                    "model_error_minutes": margin,
                    "available": bool(row[5] and row[6] and not row[7] and not row[8]),
                    "observation_fresh": fresh,
                }
            )
            if margin is not None
            else None
        )
        comparisons.append(
            ReplayComparison(
                evaluated_at=observed,
                baseline_margin_minutes=request.model_error_minutes,
                suggested_addition_minutes=addition,
                suggested_margin_minutes=margin,
                fixed_would_submit=decisions[-1].would_submit,
                suggested_would_submit=suggested.would_submit if suggested else None,
                trend=trend,
            )
        )
    return ReplayResult(
        request=request,
        decisions=decisions,
        first_would_submit_at=next(
            (item.evaluated_at for item in decisions if item.would_submit), None
        ),
        comparison=comparisons,
        first_suggested_would_submit_at=next(
            (item.evaluated_at for item in comparisons if item.suggested_would_submit), None
        ),
    )
