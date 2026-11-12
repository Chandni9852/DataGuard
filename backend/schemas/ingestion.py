import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    dataset_id: int
    dataset_name: str
    source_type: str
    created_at: datetime


class ColumnProfile(BaseModel):
    name: str
    inferred_type: str
    type_confidence: float
    null_count: int
    distinct_count: int
    sample_values: list[str]


class PreviewOut(BaseModel):
    dataset_name: str
    file_name: str
    size_bytes: int
    sha256: str
    total_rows: int
    schema_valid: bool
    required_columns: list[str]
    missing_required_columns: list[str]
    unexpected_columns: list[str]
    renamed_columns: dict[str, str]
    columns: list[ColumnProfile]
    preview_rows: list[dict[str, str]]
    warnings: list[str]


class BatchOut(BaseModel):
    batch_id: uuid.UUID
    dataset_id: int
    dataset_name: str
    source_file: str
    file_sha256: str | None
    file_size_bytes: int | None
    ingestion_timestamp: datetime
    total_records: int
    processing_status: str
    uploaded_by: str
    destination_table: str | None
    completed_at: datetime | None
    warnings: list[str] = []


class ErrorOut(BaseModel):
    detail: str
    code: str | None = None
