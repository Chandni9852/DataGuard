"""ORM models. These mirror db/schema.sql, which is the source of truth for DDL."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Dataset(Base):
    __tablename__ = "datasets"
    dataset_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_name: Mapped[str] = mapped_column(String(100), unique=True)
    source_type: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionBatch(Base):
    __tablename__ = "ingestion_batches"
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.dataset_id"))
    source_file: Mapped[str] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(255))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    ingestion_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    total_records: Mapped[int] = mapped_column(Integer, default=0)
    processing_status: Mapped[str] = mapped_column(String(20), default="UPLOADED")
    uploaded_by: Mapped[str] = mapped_column(String(100), default="system")
    destination_table: Mapped[str | None] = mapped_column(String(100))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    dataset: Mapped[Dataset] = relationship()


class Customer(Base):
    __tablename__ = "customers"
    customer_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    customer_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Transaction(Base):
    """Raw values stored as text so invalid data can be kept and reported."""
    __tablename__ = "transactions"
    record_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_batches.batch_id", ondelete="CASCADE"))
    source_row_number: Mapped[int] = mapped_column(Integer)
    transaction_id: Mapped[str | None] = mapped_column(Text)
    customer_id: Mapped[str | None] = mapped_column(Text)
    transaction_amount: Mapped[str | None] = mapped_column(Text)
    transaction_date: Mapped[str | None] = mapped_column(Text)
    merchant: Mapped[str | None] = mapped_column(Text)
    transaction_status: Mapped[str | None] = mapped_column(Text)


class ValidationRule(Base):
    __tablename__ = "validation_rules"
    rule_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(20), unique=True)
    rule_name: Mapped[str] = mapped_column(String(100))
    rule_category: Mapped[str] = mapped_column(String(30))
    rule_description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10))
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ValidationResult(Base):
    __tablename__ = "validation_results"
    result_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_batches.batch_id", ondelete="CASCADE"))
    record_id: Mapped[int] = mapped_column(ForeignKey("transactions.record_id", ondelete="CASCADE"))
    record_identifier: Mapped[str | None] = mapped_column(Text)
    row_number: Mapped[int | None] = mapped_column(Integer)
    rule_id: Mapped[int] = mapped_column(ForeignKey("validation_rules.rule_id"))
    column_name: Mapped[str | None] = mapped_column(String(100))
    offending_value: Mapped[str | None] = mapped_column(Text)
    error_description: Mapped[str] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QualityMetric(Base):
    __tablename__ = "quality_metrics"
    metric_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_batches.batch_id", ondelete="CASCADE"))
    completeness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    uniqueness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    validity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    consistency_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    overall_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    total_records: Mapped[int] = mapped_column(Integer, default=0)
    valid_records: Mapped[int] = mapped_column(Integer, default=0)
    invalid_records: Mapped[int] = mapped_column(Integer, default=0)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_batches.batch_id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(50))
    actor: Mapped[str] = mapped_column(String(100), default="system")
    action_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    execution_status: Mapped[str] = mapped_column(String(20))
    rules_executed: Mapped[list | None] = mapped_column(JSONB)
    records_processed: Mapped[int | None] = mapped_column(Integer)
    failure_count: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSONB)


class ExceptionReview(Base):
    __tablename__ = "exception_reviews"
    review_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("validation_results.result_id", ondelete="CASCADE"))
    review_status: Mapped[str] = mapped_column(String(15), default="OPEN")
    reviewer: Mapped[str | None] = mapped_column(String(100))
    comments: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
