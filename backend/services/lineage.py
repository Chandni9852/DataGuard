"""Data lineage: trace any batch, or any single failure, back to its original source file.

Batch lineage answers: where did this data come from (original file name, hash, size, who
uploaded it, when), where did it go (destination table, record count), what happened to it
(ordered audit timeline) and what came out of it (failures, review progress, quality score).

Exception lineage goes one step further. It re-reads the preserved original upload, finds the
row the failure points to, and compares it with what is stored in the database. That is the
end-to-end proof that a reported failure really corresponds to a row of the source file.

The preserved file is verified by recomputing its SHA-256 and comparing it with the hash
recorded at ingestion. Verification is evidence, not enforcement: a mismatch or a missing file
is reported, never silently ignored and never "repaired".
"""
import csv
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.errors import DataGuardError
from backend.models import AuditLog, IngestionBatch, Transaction, ValidationResult
from backend.services import exceptions as exc_svc
from backend.services import ingestion, quality_scoring
from backend.services.rules.base import TRANSACTION_COLUMNS
from backend.utils import file_checks

TIMELINE_LIMIT = 500


def stored_path(batch: IngestionBatch) -> Path:
    return Path(get_settings().upload_dir) / Path(batch.file_name).name      # never trust path parts


def file_status(batch: IngestionBatch, verify: bool) -> str:
    if not verify:
        return "not_checked"
    path = stored_path(batch)
    if not path.is_file():
        return "missing"
    return "verified" if file_checks.sha256_hex(path.read_bytes()) == batch.file_sha256 else "hash_mismatch"


def original_row(path: Path, row_number: int) -> dict[str, str] | None:
    """The row_number-th data record (1-based, blank lines skipped) of the file, keyed by
    normalised header. Mirrors how ingestion numbers rows. None if the file has no such row."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = [ingestion._normalise_header(h) for h in next(reader)]
        seen = 0
        for row in reader:
            if not row:
                continue
            seen += 1
            if seen == row_number:
                return dict(zip(header, row))
    return None


def matches_original(batch: IngestionBatch, row_number: int, stored: dict) -> bool | None:
    """True/False if the stored record equals the original file row; None if the file is gone."""
    path = stored_path(batch)
    if not path.is_file():
        return None
    try:
        orig = original_row(path, row_number)
    except (UnicodeDecodeError, csv.Error, StopIteration):
        return False
    if orig is None:
        return False
    return all((orig.get(c) or "") == (stored.get(c) or "") for c in TRANSACTION_COLUMNS)


# ----------------------------------------------------------------- batch lineage

def batch_lineage(session: Session, batch_id: uuid.UUID, verify_file: bool = True) -> dict:
    batch, dataset_name = ingestion.get_batch(session, batch_id)
    loaded = None
    if batch.destination_table == "transactions":
        loaded = session.scalar(select(func.count()).select_from(Transaction)
                                .where(Transaction.batch_id == batch_id)) or 0
    failures = session.scalar(select(func.count()).select_from(ValidationResult)
                              .where(ValidationResult.batch_id == batch_id)) or 0
    metric = quality_scoring.latest_metric(session, batch_id)
    timeline = session.execute(
        select(AuditLog.log_id, AuditLog.action, AuditLog.actor, AuditLog.action_timestamp.label("timestamp"),
               AuditLog.execution_status.label("status"), AuditLog.records_processed, AuditLog.failure_count,
               AuditLog.duration_ms)
        .where(AuditLog.batch_id == batch_id).order_by(AuditLog.log_id).limit(TIMELINE_LIMIT)).all()
    return {
        "batch_id": batch.batch_id, "dataset_id": batch.dataset_id, "dataset_name": dataset_name,
        "processing_status": batch.processing_status, "ingestion_timestamp": batch.ingestion_timestamp,
        "completed_at": batch.completed_at, "uploaded_by": batch.uploaded_by,
        "source": {"original_file_name": batch.source_file, "stored_file_name": batch.file_name,
                   "size_bytes": batch.file_size_bytes, "sha256": batch.file_sha256,
                   "stored_file_status": file_status(batch, verify_file)},
        "destination": {"table": batch.destination_table, "records_expected": batch.total_records,
                        "records_loaded": loaded,
                        "loaded_records_match": None if loaded is None else loaded == batch.total_records},
        "downstream": {"validation_failures": failures,
                       "exceptions_by_review_status": exc_svc.summary(session, exc_svc.Filters(batch_id=batch_id))["by_review_status"],
                       "latest_quality": quality_scoring.metric_to_dict(metric) if metric else None},
        "timeline": [dict(r._mapping) for r in timeline],
    }


# ----------------------------------------------------------------- exception lineage

def exception_lineage(session: Session, result_id: int, verify_file: bool = True) -> dict:
    d = exc_svc.get_exception(session, result_id)                 # 404 if unknown
    batch, _ = ingestion.get_batch(session, d["batch_id"])
    record = d["record"]
    return {
        "exception": {k: d[k] for k in ("result_id", "rule_code", "rule_name", "category", "severity", "column_name",
                                        "offending_value", "error_description", "detected_at", "record_identifier",
                                        "review_status")},
        "source_record": {"row_number": record["source_row_number"],
                          "values": {c: record[c] for c in TRANSACTION_COLUMNS}},
        "matches_original_file": matches_original(batch, record["source_row_number"], record) if verify_file else None,
        "batch": batch_lineage(session, d["batch_id"], verify_file),
    }
