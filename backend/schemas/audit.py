import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from backend.schemas.quality import QualityOut


class AuditLogOut(BaseModel):
    log_id: int
    batch_id: uuid.UUID | None
    action: str
    actor: str
    action_timestamp: datetime
    execution_status: str
    rules_executed: list | None
    records_processed: int | None
    failure_count: int | None
    duration_ms: int | None
    details: dict | None
    source_file: str | None            # of the related batch, when there is one
    dataset_name: str | None


class SourceInfo(BaseModel):
    original_file_name: str
    stored_file_name: str | None
    size_bytes: int | None
    sha256: str | None
    stored_file_status: Literal["verified", "hash_mismatch", "missing", "not_checked"]


class DestinationInfo(BaseModel):
    table: str | None
    records_expected: int
    records_loaded: int | None
    loaded_records_match: bool | None


class DownstreamInfo(BaseModel):
    validation_failures: int
    exceptions_by_review_status: dict[str, int]
    latest_quality: QualityOut | None


class TimelineEntry(BaseModel):
    log_id: int
    action: str
    actor: str
    timestamp: datetime
    status: str
    records_processed: int | None
    failure_count: int | None
    duration_ms: int | None


class BatchLineage(BaseModel):
    batch_id: uuid.UUID
    dataset_id: int
    dataset_name: str
    processing_status: str
    ingestion_timestamp: datetime
    completed_at: datetime | None
    uploaded_by: str
    source: SourceInfo
    destination: DestinationInfo
    downstream: DownstreamInfo
    timeline: list[TimelineEntry]      # oldest first


class ExceptionRef(BaseModel):
    result_id: int
    rule_code: str
    rule_name: str
    category: str
    severity: str
    column_name: str | None
    offending_value: str | None
    error_description: str
    detected_at: datetime
    record_identifier: str | None
    review_status: str


class SourceRecord(BaseModel):
    row_number: int
    values: dict[str, str | None]


class ExceptionLineage(BaseModel):
    exception: ExceptionRef
    source_record: SourceRecord
    matches_original_file: bool | None      # None: file missing or not checked
    batch: BatchLineage
