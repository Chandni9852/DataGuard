"""Module 3: data quality scoring.

Methodology (the numbers a reader can verify by hand):

  Completeness = non-missing mandatory cells / expected cells x 100
                 expected cells = records x mandatory columns covered by an active completeness rule
  Uniqueness   = records with no UNIQUENESS failure / records x 100
  Validity     = records with no VALIDITY failure / records x 100
  Consistency  = records with no CONSISTENCY or REFERENTIAL_INTEGRITY failure / records x 100
  Overall DQS  = mean of the dimension scores that were assessed

- No double counting: Uniqueness, Validity and Consistency count each *record* once per
  dimension however many rules of that dimension it fails. A record can still lower more than
  one dimension, because it genuinely has more than one kind of problem. Completeness counts
  each missing *cell* once.
- Referential integrity is part of Consistency, so the four dimensions match the formula in the
  project brief (the brief names five rule categories but four scores).
- A dimension is "assessed" only if at least one active rule of its category ran. If an admin
  switched every uniqueness rule off, uniqueness is reported as NULL instead of a misleading
  100, and the overall score averages the remaining dimensions. With all four assessed this is
  exactly (C + U + V + K) / 4 with equal weights.
- Stored scores are rounded half-up to 2 decimals, and Overall is the mean of the *rounded*
  dimension scores, so it always equals the average of the four numbers shown on screen.
- An empty batch has no scores at all (NULL), never 100%.
- Only rules that actually ran for the batch count. Results are filtered to those rules, so a
  rule switched off later cannot change a batch's score.
"""
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from backend.errors import DataGuardError
from backend.models import AuditLog, IngestionBatch, QualityMetric, ValidationResult, ValidationRule
from backend.services import ingestion
from backend.services.rules import REGISTRY
from backend.utils.time import utcnow

CENT = Decimal("0.01")
DIMENSIONS = ("completeness", "uniqueness", "validity", "consistency")
CONSISTENCY_CATEGORIES = ("CONSISTENCY", "REFERENTIAL_INTEGRITY")


@dataclass(frozen=True)
class ScoreInputs:
    total_records: int
    invalid_records: int                  # records with at least one failure of any kind
    completeness_columns: int             # mandatory columns covered by active completeness rules
    missing_cells: int
    uniqueness_assessed: bool = False
    uniqueness_failed_records: int = 0
    validity_assessed: bool = False
    validity_failed_records: int = 0
    consistency_assessed: bool = False
    consistency_failed_records: int = 0


def _pct(good: int, total: int) -> Decimal:
    return (Decimal(100) * good / total).quantize(CENT, rounding=ROUND_HALF_UP)


def compute_scores(i: ScoreInputs) -> dict:
    """Pure function. Returns Decimal scores (None where not assessed) and record counts."""
    scores: dict[str, Decimal | None] = {d: None for d in DIMENSIONS}
    if i.total_records > 0:
        cells = i.total_records * i.completeness_columns
        if i.completeness_columns > 0:
            scores["completeness"] = _pct(cells - min(i.missing_cells, cells), cells)
        if i.uniqueness_assessed:
            scores["uniqueness"] = _pct(i.total_records - i.uniqueness_failed_records, i.total_records)
        if i.validity_assessed:
            scores["validity"] = _pct(i.total_records - i.validity_failed_records, i.total_records)
        if i.consistency_assessed:
            scores["consistency"] = _pct(i.total_records - i.consistency_failed_records, i.total_records)
    assessed = [v for v in scores.values() if v is not None]
    overall = (sum(assessed) / len(assessed)).quantize(CENT, rounding=ROUND_HALF_UP) if assessed else None
    return {
        **scores, "overall": overall,
        "total_records": i.total_records,
        "invalid_records": i.invalid_records,
        "valid_records": i.total_records - i.invalid_records,
    }


# ---------------------------------------------------------------- database side

def executed_rules_from_rules(rules) -> list[dict]:
    return [{"code": r.code, "category": r.category, "column": r.column} for r in rules]


def executed_rules_from_audit(session: Session, batch_id: uuid.UUID) -> list[dict]:
    """The rules that ran for a batch, taken from its successful validation audit entry."""
    entry = session.scalar(
        select(AuditLog.rules_executed)
        .where(AuditLog.batch_id == batch_id, AuditLog.action == "VALIDATE", AuditLog.execution_status == "SUCCESS")
        .order_by(AuditLog.log_id.desc()).limit(1))
    if not entry:
        raise DataGuardError("conflict", "This batch has no completed validation run to score.")
    codes = [e["rule_code"] for e in entry]
    rows = {r.rule_code: r for r in session.scalars(select(ValidationRule).where(ValidationRule.rule_code.in_(codes)))}
    out = []
    for code in codes:
        row = rows.get(code)
        if row is None:
            continue
        params = {**REGISTRY[code].defaults(code), **(row.parameters or {})} if code in REGISTRY else (row.parameters or {})
        out.append({"code": code, "category": row.rule_category, "column": params.get("column")})
    return out


def _distinct_records(session: Session, batch_id, codes: list[str], categories: tuple[str, ...] | None) -> int:
    q = (select(func.count(distinct(ValidationResult.record_id)))
         .join(ValidationRule, ValidationRule.rule_id == ValidationResult.rule_id)
         .where(ValidationResult.batch_id == batch_id, ValidationRule.rule_code.in_(codes)))
    if categories:
        q = q.where(ValidationRule.rule_category.in_(categories))
    return session.scalar(q) or 0


def gather_inputs(session: Session, batch_id: uuid.UUID, total_records: int, executed: list[dict]) -> ScoreInputs:
    codes = [e["code"] for e in executed]
    cats = {e["category"] for e in executed}
    comp_cols = {e["column"] for e in executed if e["category"] == "COMPLETENESS" and e["column"]}
    missing_cells = 0
    if codes and comp_cols:
        cells = (select(ValidationResult.record_id, ValidationResult.column_name).distinct()
                 .join(ValidationRule, ValidationRule.rule_id == ValidationResult.rule_id)
                 .where(ValidationResult.batch_id == batch_id, ValidationRule.rule_code.in_(codes),
                        ValidationRule.rule_category == "COMPLETENESS").subquery())
        missing_cells = session.scalar(select(func.count()).select_from(cells)) or 0

    def failed(categories):
        return _distinct_records(session, batch_id, codes, categories) if codes else 0

    return ScoreInputs(
        total_records=total_records,
        invalid_records=failed(None),
        completeness_columns=len(comp_cols),
        missing_cells=missing_cells,
        uniqueness_assessed="UNIQUENESS" in cats,
        uniqueness_failed_records=failed(("UNIQUENESS",)),
        validity_assessed="VALIDITY" in cats,
        validity_failed_records=failed(("VALIDITY",)),
        consistency_assessed=bool(cats & set(CONSISTENCY_CATEGORIES)),
        consistency_failed_records=failed(CONSISTENCY_CATEGORIES),
    )


def calculate_and_store(session: Session, batch_id: uuid.UUID, total_records: int, executed: list[dict]) -> QualityMetric:
    """Compute scores from stored results and add a new quality_metrics row. Caller commits.

    Each call appends a row, so history is kept; the latest row is the current score."""
    s = compute_scores(gather_inputs(session, batch_id, total_records, executed))
    metric = QualityMetric(
        batch_id=batch_id, completeness_score=s["completeness"], uniqueness_score=s["uniqueness"],
        validity_score=s["validity"], consistency_score=s["consistency"], overall_score=s["overall"],
        total_records=s["total_records"], valid_records=s["valid_records"], invalid_records=s["invalid_records"],
        calculated_at=utcnow())
    session.add(metric)
    session.flush()
    return metric


# ---------------------------------------------------------------- queries

def metric_to_dict(m: QualityMetric) -> dict:
    f = lambda v: None if v is None else float(v)  # noqa: E731
    scores = {d: f(getattr(m, f"{d}_score")) for d in DIMENSIONS}
    return {
        "metric_id": m.metric_id, "batch_id": m.batch_id,
        **{f"{d}_score": v for d, v in scores.items()}, "overall_score": f(m.overall_score),
        "total_records": m.total_records, "valid_records": m.valid_records, "invalid_records": m.invalid_records,
        "calculated_at": m.calculated_at,
        "status": "scored" if m.overall_score is not None else "no_data",
        "dimensions_assessed": [d for d in DIMENSIONS if scores[d] is not None],
    }


def latest_metric(session: Session, batch_id: uuid.UUID) -> QualityMetric | None:
    return session.scalar(select(QualityMetric).where(QualityMetric.batch_id == batch_id)
                          .order_by(QualityMetric.calculated_at.desc(), QualityMetric.metric_id.desc()).limit(1))


def get_quality(session: Session, batch_id: uuid.UUID) -> dict:
    ingestion.get_batch(session, batch_id)                       # 404 if the batch is unknown
    m = latest_metric(session, batch_id)
    if m is None:
        raise DataGuardError("not_found", "This batch has not been scored yet. Validate it first.")
    return metric_to_dict(m)


def get_history(session: Session, batch_id: uuid.UUID) -> list[dict]:
    ingestion.get_batch(session, batch_id)
    rows = session.scalars(select(QualityMetric).where(QualityMetric.batch_id == batch_id)
                           .order_by(QualityMetric.calculated_at.desc(), QualityMetric.metric_id.desc()))
    return [metric_to_dict(m) for m in rows]


def recalculate(session: Session, batch_id: uuid.UUID, actor: str | None = None) -> dict:
    """Score a completed batch again from its stored results (e.g. after a methodology change)."""
    from backend.services import audit
    actor = ingestion._clean_actor(actor)
    batch, _ = ingestion.get_batch(session, batch_id)
    if batch.processing_status != "COMPLETED":
        raise DataGuardError("conflict", f"Batch is {batch.processing_status}; only completed batches can be scored.")
    executed = executed_rules_from_audit(session, batch_id)
    m = calculate_and_store(session, batch_id, batch.total_records, executed)
    audit.log_action(session, "SCORE", "SUCCESS", batch_id=batch_id, actor=actor,
                     records_processed=batch.total_records, details={"metric_id": m.metric_id})
    session.commit()
    return metric_to_dict(m)


def trend(session: Session, dataset_id: int | None, limit: int) -> list[dict]:
    """Latest score of each batch, oldest first, for charting quality over time."""
    latest = (select(QualityMetric.batch_id, func.max(QualityMetric.metric_id).label("mid"))
              .group_by(QualityMetric.batch_id).subquery())
    q = (select(QualityMetric, IngestionBatch.source_file, IngestionBatch.ingestion_timestamp)
         .join(latest, latest.c.mid == QualityMetric.metric_id)
         .join(IngestionBatch, IngestionBatch.batch_id == QualityMetric.batch_id)
         .order_by(IngestionBatch.ingestion_timestamp.desc()).limit(limit))
    if dataset_id is not None:
        q = q.where(IngestionBatch.dataset_id == dataset_id)
    rows = list(session.execute(q).all())
    rows.reverse()
    return [{**metric_to_dict(m), "source_file": src, "ingestion_timestamp": ts} for m, src, ts in rows]
