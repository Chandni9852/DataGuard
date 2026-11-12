import re

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.api.exceptions import get_filters
from backend.database import get_db, get_session_factory
from backend.schemas.ingestion import ErrorOut
from backend.services import audit
from backend.services import exceptions as svc
from backend.utils.time import utcnow

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/exceptions.csv", responses={404: {"model": ErrorOut}},
            response_class=StreamingResponse)
def export_exceptions(f: svc.Filters = Depends(get_filters), db: Session = Depends(get_db)):
    """Download the exceptions matching the filters as CSV (formula-injection safe)."""
    svc.check_filters(db, f)                                   # 404 before any streaming starts
    audit.log_action(db, "EXPORT_EXCEPTIONS", "SUCCESS", batch_id=f.batch_id,
                     details={"filters": {k: str(v) for k, v in vars(f).items() if v}})
    db.commit()

    tag = str(f.batch_id)[:8] if f.batch_id else "all"
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", f"dataguard_exceptions_{tag}_{utcnow():%Y%m%d}.csv")

    def stream():
        # The generator owns its session: the request-scoped one may be closed before streaming ends.
        session = get_session_factory()()
        try:
            yield from svc.csv_chunks(session, f)
        finally:
            session.close()

    return StreamingResponse(stream(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})
