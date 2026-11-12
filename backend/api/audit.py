import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas.audit import AuditLogOut
from backend.schemas.ingestion import ErrorOut
from backend.services import audit

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=list[AuditLogOut], responses={404: {"model": ErrorOut}, 422: {"model": ErrorOut}})
def list_audit_logs(
    response: Response,
    batch_id: uuid.UUID | None = None,
    action: str | None = Query(None, max_length=50, description="e.g. INGEST, VALIDATE, SCORE, EXCEPTION_REVIEW"),
    status: Literal["STARTED", "SUCCESS", "FAILED"] | None = None,
    actor: str | None = Query(None, max_length=100),
    since: datetime | None = Query(None, description="Inclusive. Timestamps without a zone are taken as UTC."),
    until: datetime | None = Query(None, description="Inclusive. Timestamps without a zone are taken as UTC."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """The audit trail, newest first. Read-only: audit entries can never be changed or deleted."""
    rows, total = audit.list_logs(db, audit.AuditFilters(batch_id, action, status, actor, since, until), limit, offset)
    response.headers["X-Total-Count"] = str(total)
    return rows


# Declared before /{log_id} so "actions" is not parsed as an id.
@router.get("/actions", response_model=list[str])
def list_actions(db: Session = Depends(get_db)):
    """Distinct action names present in the audit trail (for filter menus)."""
    return audit.list_actions(db)


@router.get("/{log_id}", response_model=AuditLogOut, responses={404: {"model": ErrorOut}})
def get_audit_log(log_id: int, db: Session = Depends(get_db)):
    return audit.get_log(db, log_id)
