import uuid
from datetime import datetime

from pydantic import BaseModel


class QualityOut(BaseModel):
    metric_id: int
    batch_id: uuid.UUID
    completeness_score: float | None
    uniqueness_score: float | None
    validity_score: float | None
    consistency_score: float | None
    overall_score: float | None
    total_records: int
    valid_records: int
    invalid_records: int
    calculated_at: datetime
    status: str                       # "scored" or "no_data"
    dimensions_assessed: list[str]


class TrendPoint(QualityOut):
    source_file: str
    ingestion_timestamp: datetime


class RecalculateRequest(BaseModel):
    requested_by: str | None = None
