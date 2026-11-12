import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["COMPLETENESS", "UNIQUENESS", "VALIDITY", "CONSISTENCY", "REFERENTIAL_INTEGRITY"]
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
ReviewStatus = Literal["OPEN", "IN_REVIEW", "REVIEWED", "WAIVED"]
SortKey = Literal["severity", "detected", "row", "rule"]


class ExceptionOut(BaseModel):
    result_id: int
    batch_id: uuid.UUID
    source_file: str
    record_id: int
    record_identifier: str | None
    row_number: int | None
    rule_code: str
    rule_name: str
    category: str
    severity: str
    column_name: str | None
    offending_value: str | None
    error_description: str
    detected_at: datetime
    review_status: str
    reviewer: str | None
    reviewed_at: datetime | None
    last_comment: str | None


class ReviewOut(BaseModel):
    review_id: int
    result_id: int
    review_status: str
    reviewer: str | None
    comments: str | None
    reviewed_at: datetime


class ExceptionDetail(ExceptionOut):
    record: dict[str, str | int | None]        # the raw source record exactly as ingested
    history: list[ReviewOut]                   # newest first


class ReviewIn(BaseModel):
    status: ReviewStatus
    reviewer: str = Field(..., max_length=100)
    comments: str | None = Field(None, max_length=2000)


class BulkReviewIn(ReviewIn):
    result_ids: list[int] = Field(..., min_length=1, max_length=500)


class BulkReviewOut(BaseModel):
    updated: int
    skipped_unchanged: int
    result_ids: list[int]


class ExceptionSummary(BaseModel):
    total: int
    by_review_status: dict[str, int]
    by_category: dict[str, int]
    by_severity: dict[str, int]
