from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas.quality import TrendPoint
from backend.services import quality_scoring

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/trend", response_model=list[TrendPoint])
def quality_trend(dataset_id: int | None = None, limit: int = Query(50, ge=1, le=500),
                  db: Session = Depends(get_db)):
    """Latest score of each batch in ingestion order (oldest first), for trend charts."""
    return quality_scoring.trend(db, dataset_id, limit)
