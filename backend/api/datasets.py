from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from backend.api.deps import read_upload
from backend.database import get_db
from backend.schemas.ingestion import BatchOut, DatasetOut, ErrorOut, PreviewOut
from backend.services import ingestion

router = APIRouter(prefix="/datasets", tags=["datasets"])

_ERRORS = {
    404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 413: {"model": ErrorOut},
    415: {"model": ErrorOut}, 422: {"model": ErrorOut},
}


@router.get("", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db)):
    return ingestion.list_datasets(db)


@router.post("/{dataset_id}/preview", response_model=PreviewOut, responses=_ERRORS)
def preview_upload(
    dataset_id: int,
    file: UploadFile = File(...),
    rows: int = Query(20, ge=0, le=100, description="Number of preview rows to return"),
    db: Session = Depends(get_db),
):
    """Parse and profile a CSV without saving anything."""
    data = read_upload(file)
    return ingestion.preview(db, dataset_id, file.filename, file.content_type, data, rows)


@router.post("/{dataset_id}/batches", response_model=BatchOut, status_code=status.HTTP_201_CREATED,
             responses=_ERRORS)
def create_batch(
    dataset_id: int,
    file: UploadFile = File(...),
    uploaded_by: str | None = Form(None),
    force: bool = Form(False, description="Ingest even if an identical file was ingested before"),
    db: Session = Depends(get_db),
):
    """Ingest a CSV as a new batch."""
    data = read_upload(file)
    batch, warnings = ingestion.ingest(db, dataset_id, file.filename, file.content_type, data,
                                       uploaded_by=uploaded_by, force=force)
    b, dataset_name = ingestion.get_batch(db, batch.batch_id)
    return _batch_out(b, dataset_name, warnings)


def _batch_out(b, dataset_name: str, warnings: list[str] | None = None) -> BatchOut:
    return BatchOut(
        batch_id=b.batch_id, dataset_id=b.dataset_id, dataset_name=dataset_name,
        source_file=b.source_file, file_sha256=b.file_sha256, file_size_bytes=b.file_size_bytes,
        ingestion_timestamp=b.ingestion_timestamp, total_records=b.total_records,
        processing_status=b.processing_status, uploaded_by=b.uploaded_by,
        destination_table=b.destination_table, completed_at=b.completed_at,
        warnings=warnings or [])
