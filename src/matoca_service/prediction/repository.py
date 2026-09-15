import sqlite3
from datetime import datetime

from matoca_service.prediction.model import predict
from matoca_service.prediction.models import Prediction, PredictionSample
from matoca_service.storage.database import Database


class PredictionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def samples(self, merchant_key: str, at: datetime) -> list[PredictionSample]:
        def read(connection: sqlite3.Connection) -> list[PredictionSample]:
            rows = connection.execute(
                """SELECT merchant_key, shop_id, submitted_at, called_at,
                          official_minutes_at_submission
                   FROM queue_sessions
                   WHERE merchant_key=? AND status='called' AND source != 'adopted'
                     AND submitted_at IS NOT NULL AND called_at IS NOT NULL
                     AND official_minutes_at_submission > 0
                     AND official_is_more_at_submission = 0
                     AND submitted_at <= ?
                     AND called_at <= ?
                     AND EXISTS (
                         SELECT 1 FROM queue_session_observations o
                         WHERE o.session_id=queue_sessions.session_id AND o.count=0
                           AND o.observed_minute=queue_sessions.called_at
                     )
                     AND (called_at >= submitted_at OR
                          substr(called_at, 1, 16) = substr(submitted_at, 1, 16))""",
                (merchant_key, at.isoformat(), at.isoformat()),
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
