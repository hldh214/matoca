import sqlite3
from datetime import datetime
from typing import Literal

from matoca_service.prediction.model import predict
from matoca_service.prediction.models import Prediction, PredictionSample
from matoca_service.storage.database import Database


class PredictionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def samples(
        self, merchant_key: str, at: datetime, *, target: Literal["calling", "pre_call"] = "calling"
    ) -> list[PredictionSample]:
        def read(connection: sqlite3.Connection) -> list[PredictionSample]:
            rows = connection.execute(
                """SELECT q.merchant_key, q.shop_id, q.submitted_at, m.occurred_at,
                          q.official_minutes_at_submission
                   FROM queue_sessions q JOIN queue_milestones m ON m.session_id=q.session_id
                   WHERE q.merchant_key=? AND m.kind=? AND q.source != 'adopted'
                     AND q.submitted_at IS NOT NULL AND q.shop_id IS NOT NULL
                     AND q.official_minutes_at_submission > 0
                     AND q.official_is_more_at_submission=0
                     AND julianday(m.occurred_at)>=julianday(q.submitted_at)-1.0/1440
                     AND julianday(m.occurred_at)<=julianday(?)
                     AND julianday(m.recorded_at)<=julianday(?)
                     AND NOT EXISTS (
                       SELECT 1 FROM queue_milestones preferred
                       WHERE preferred.session_id=m.session_id AND preferred.kind=m.kind
                       AND preferred.source='line_notification_manual'
                       AND m.source='api_observation'
                       AND julianday(preferred.recorded_at)<=julianday(?)
                     ) ORDER BY q.session_id""",
                (merchant_key, target, at.isoformat(), at.isoformat(), at.isoformat()),
            ).fetchall()
            return [
                PredictionSample(
                    merchant_key=str(row[0]),
                    shop_id=int(row[1]),
                    submitted_at=datetime.fromisoformat(str(row[2])),
                    ratio=max(
                        0,
                        (
                            datetime.fromisoformat(str(row[3]))
                            - datetime.fromisoformat(str(row[2]))
                        ).total_seconds(),
                    )
                    / 60
                    / int(row[4]),
                )
                for row in rows
                if row[1] is not None
            ]

        return self._database.read(read)


class PredictionService:
    """Stable prediction entry point for console and automation callers."""

    def __init__(self, repository: PredictionRepository) -> None:
        self._repository = repository

    def predict_pre_call(
        self,
        merchant_key: str,
        shop_id: int,
        official_minutes: int | None,
        at: datetime,
    ) -> Prediction | None:
        # Pre-call policies may differ by shop; never transfer a single shop's
        # notification lead time to every merchant location.
        samples = [
            s
            for s in self._repository.samples(merchant_key, at, target="pre_call")
            if s.shop_id == shop_id
        ]
        result = predict(merchant_key, shop_id, official_minutes, at, samples)
        if result is None:
            return None
        if not samples:
            return result.model_copy(
                update={"fast_minutes": 0, "typical_minutes": 0, "target": "pre_call"}
            )
        return result.model_copy(update={"target": "pre_call"})

    def predict(
        self,
        merchant_key: str,
        shop_id: int,
        official_minutes: int | None,
        at: datetime,
    ) -> Prediction | None:
        return predict(
            merchant_key,
            shop_id,
            official_minutes,
            at,
            self._repository.samples(merchant_key, at),
        )
