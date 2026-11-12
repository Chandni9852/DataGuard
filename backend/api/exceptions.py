import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas.exceptions import (BulkReviewIn, BulkReviewOut, Category, ExceptionDetail, ExceptionOut,
                                        ExceptionSummary, ReviewIn, ReviewOut, ReviewStatus, Severity, SortKey)
from backend.schemas.audit import ExceptionLineage
from backend.schemas.ingestion import ErrorOut
from backend.services import exceptions as svc
from backend.services import lineage

router = APIRouter(prefix="/exceptions", tags=["exceptions"])
_ERR = {404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}}


def get_filters(
    batch_id: uuid.UUID | None = None,
    category: Category | None = None,
    severity: Severity | None = None,
    rule_code: str | None = Query(None, max_length=20),
    review_status: ReviewStatus | None = None,
    q: str | None = Query(None, max_length=100, description="Record identifier contains (case-insensitive)"),
) -> svc.Filters:
    return svc.Filters(batch_id, category, severity, rule_code, review_status, q)


@router.get("", response_model=list[ExceptionOut], responses={404: {"model": ErrorOut}})
def list_exceptions(
    response: Response,
    f: svc.Filters = Depends(get_filters),
    sort: SortKey = "severity",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Failed records, filterable by batch, category, severity, rule, review status and record ID."""
    rows, total = svc.list_exceptions(db, f, sort, limit, offset)
    response.headers["X-Total-Count"] = str(total)
    return rows


# Static paths are declared before /{result_id} so they are not parsed as an id.
@router.get("/summary", response_model=ExceptionSummary, responses={404: {"model": ErrorOut}})
def exception_summary(f: svc.Filters = Depends(get_filters), db: Session = Depends(get_db)):
    """Counts by review status, category and severity for the same filters as the list."""
    return svc.summary(db, f)


@router.post("/review-bulk", response_model=BulkReviewOut, responses=_ERR)
def review_bulk(body: BulkReviewIn, db: Session = Depends(get_db)):
    """Apply one decision to many exceptions, all or nothing. Already-in-that-status ones are skipped."""
    return svc.review_bulk(db, body.result_ids, body.status, body.reviewer, body.comments)


@router.get("/{result_id}", response_model=ExceptionDetail, responses={404: {"model": ErrorOut}})
def get_exception(result_id: int, db: Session = Depends(get_db)):
    """One exception with its raw source record and review history."""
    return svc.get_exception(db, result_id)


@router.get("/{result_id}/lineage", response_model=ExceptionLineage, responses={404: {"model": ErrorOut}})
def exception_lineage(result_id: int, verify_file: bool = Query(True, description="Compare with the preserved original file"),
                      db: Session = Depends(get_db)):
    """Trace a failure back to its source row, original file and batch."""
    return lineage.exception_lineage(db, result_id, verify_file)


@router.get("/{result_id}/history", response_model=list[ReviewOut], responses={404: {"model": ErrorOut}})
def exception_history(result_id: int, db: Session = Depends(get_db)):
    return svc.get_history(db, result_id)


@router.post("/{result_id}/review", response_model=ExceptionDetail, responses=_ERR)
def review_exception(result_id: int, body: ReviewIn, db: Session = Depends(get_db)):
    """Record a review decision. Append-only: earlier decisions are kept in the history."""
    return svc.review(db, result_id, body.status, body.reviewer, body.comments)
