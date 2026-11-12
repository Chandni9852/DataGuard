"""Audit trail: writer and query side.

Audit rows are append-only (the database refuses UPDATE and DELETE) and never contain record
contents, only counts, file metadata, rule codes and reasons. Failed ingestions are audited
with batch_id NULL because no batch exists yet; they are found with status=FAILED.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from backend.errors import DataGuardError
from backend.models import AuditLog, Dataset, IngestionBatch


def log_action(
    session: Session,
    action: str,
    status: str,
    *,
    batch_id: uuid.UUID | None = None,
    actor: str = "system",
    rules_executed: list | None = None,
    records_processed: int | None = None,
    failure_count: int | None = None,
    duration_ms: int | None = None,
    details: dict | None = None,
) -> AuditLog:
    """Add an audit row to the session. The caller controls the commit."""
    entry = AuditLog(
        batch_id=batch_id, action=action, actor=actor, execution_status=status,
        rules_executed=rules_executed, records_processed=records_processed,
        failure_count=failure_count, duration_ms=duration_ms, details=details,
    )
    session.add(entry)
    return entry


# ----------------------------------------------------------------- queries

@dataclass
class AuditFilters:
    batch_id: uuid.UUID | None = None
    action: str | None = None
    status: str | None = None
    actor: str | None = None
    since: datetime | None = None         # inclusive
    until: datetime | None = None         # inclusive


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


_COLUMNS = (
    AuditLog.log_id, AuditLog.batch_id, AuditLog.action, AuditLog.actor, AuditLog.action_timestamp,
    AuditLog.execution_status, AuditLog.rules_executed, AuditLog.records_processed, AuditLog.failure_count,
    AuditLog.duration_ms, AuditLog.details, IngestionBatch.source_file, Dataset.dataset_name,
)


def _base(columns, f: AuditFilters):
    q = (select(*columns).select_from(AuditLog)
         .outerjoin(IngestionBatch, IngestionBatch.batch_id == AuditLog.batch_id)
         .outerjoin(Dataset, Dataset.dataset_id == IngestionBatch.dataset_id))
    if f.batch_id:
        q = q.where(AuditLog.batch_id == f.batch_id)
    if f.action:
        q = q.where(AuditLog.action == f.action)
    if f.status:
        q = q.where(AuditLog.execution_status == f.status)
    if f.actor:
        q = q.where(AuditLog.actor == f.actor)
    if f.since:
        q = q.where(AuditLog.action_timestamp >= _utc(f.since))
    if f.until:
        q = q.where(AuditLog.action_timestamp <= _utc(f.until))
    return q


def list_logs(session: Session, f: AuditFilters, limit: int, offset: int) -> tuple[list[dict], int]:
    if f.batch_id:
        from backend.services import ingestion
        ingestion.get_batch(session, f.batch_id)               # 404 for an unknown batch
    if f.since and f.until and _utc(f.since) > _utc(f.until):
        raise DataGuardError("malformed", "'since' must not be later than 'until'.")
    total = session.scalar(select(func.count()).select_from(_base((AuditLog.log_id,), f).subquery())) or 0
    rows = session.execute(_base(_COLUMNS, f).order_by(AuditLog.log_id.desc()).limit(limit).offset(offset)).all()
    return [dict(r._mapping) for r in rows], total


def get_log(session: Session, log_id: int) -> dict:
    row = session.execute(_base(_COLUMNS, AuditFilters()).where(AuditLog.log_id == log_id)).first()
    if row is None:
        raise DataGuardError("not_found", f"Audit entry {log_id} does not exist.")
    return dict(row._mapping)


def list_actions(session: Session) -> list[str]:
    return list(session.scalars(select(distinct(AuditLog.action)).order_by(AuditLog.action)))
