import uuid

from fastapi import APIRouter, Body, Depends, Query, Response
from sqlalchemy.orm import Session

from backend.api.datasets import _batch_out
from backend.database import get_db
from backend.schemas.audit import BatchLineage
from backend.schemas.ingestion import BatchOut, ErrorOut
from backend.schemas.quality import QualityOut, RecalculateRequest
from backend.schemas.validation import ValidateRequest, ValidationSummary
from backend.services import ingestion, lineage, quality_scoring, validation

router = APIRouter(prefix="/batches", tags=["batches"])


@router.get("", response_model=list[BatchOut])
def list_batches(
    response: Response,
    dataset_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    response.headers["X-Total-Count"] = str(ingestion.count_batches(db, dataset_id))
    return [_batch_out(b, name) for b, name in ingestion.list_batches(db, dataset_id, limit, offset)]


@router.get("/{batch_id}", response_model=BatchOut, responses={404: {"model": ErrorOut}})
def get_batch(batch_id: uuid.UUID, db: Session = Depends(get_db)):
    b, name = ingestion.get_batch(db, batch_id)
    return _batch_out(b, name)


_VALIDATION_ERRORS = {404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}}


@router.post("/{batch_id}/validate", response_model=ValidationSummary, responses=_VALIDATION_ERRORS)
def validate_batch(batch_id: uuid.UUID, body: ValidateRequest = Body(default_factory=ValidateRequest),
                   db: Session = Depends(get_db)):
    """Run all active rules over the batch and store the failures."""
    return validation.run_validation(db, batch_id, actor=body.requested_by)


@router.get("/{batch_id}/validation-summary", response_model=ValidationSummary,
            responses={404: {"model": ErrorOut}})
def validation_summary(batch_id: uuid.UUID, db: Session = Depends(get_db)):
    return validation.summarize(db, batch_id)


@router.get("/{batch_id}/quality", response_model=QualityOut, responses={404: {"model": ErrorOut}})
def get_quality(batch_id: uuid.UUID, db: Session = Depends(get_db)):
    """Current quality scores for a batch (the latest calculation)."""
    return quality_scoring.get_quality(db, batch_id)


@router.get("/{batch_id}/quality/history", response_model=list[QualityOut], responses={404: {"model": ErrorOut}})
def quality_history(batch_id: uuid.UUID, db: Session = Depends(get_db)):
    """Every calculation for a batch, newest first."""
    return quality_scoring.get_history(db, batch_id)


@router.post("/{batch_id}/quality/recalculate", response_model=QualityOut,
             responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}})
def recalculate_quality(batch_id: uuid.UUID, body: RecalculateRequest = Body(default_factory=RecalculateRequest),
                        db: Session = Depends(get_db)):
    """Score a completed batch again; appends a new history row."""
    return quality_scoring.recalculate(db, batch_id, actor=body.requested_by)


@router.get("/{batch_id}/lineage", response_model=BatchLineage, responses={404: {"model": ErrorOut}})
def batch_lineage(batch_id: uuid.UUID, verify_file: bool = Query(True, description="Re-hash the preserved original file"),
                  db: Session = Depends(get_db)):
    """Source file, destination, processing timeline and downstream results of a batch."""
    return lineage.batch_lineage(db, batch_id, verify_file)
