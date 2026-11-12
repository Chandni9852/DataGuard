from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Liveness plus a real database round-trip."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        # Generic message to the client; detail stays out of the response.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Database unavailable")
    return {"status": "ok", "database": "up"}
