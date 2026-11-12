from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas.ingestion import ErrorOut
from backend.schemas.validation import RuleOut, RuleUpdate
from backend.services import ingestion, validation

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
def list_rules(db: Session = Depends(get_db)):
    return validation.list_rules(db)


@router.patch("/{rule_id}", response_model=RuleOut,
              responses={404: {"model": ErrorOut}, 422: {"model": ErrorOut}})
def update_rule(rule_id: int, body: RuleUpdate, db: Session = Depends(get_db)):
    """Change severity, active flag or parameters. Affects future validation runs only."""
    actor = ingestion._clean_actor(body.updated_by)
    return validation.update_rule(db, rule_id, body.model_dump(exclude={"updated_by"}), actor)
