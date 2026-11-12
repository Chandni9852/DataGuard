"""Module 2: validation engine orchestration.

    run_validation:  pre-checks -> claim batch (UPLOADED/FAILED -> VALIDATING, committed)
                     -> load rows -> run rules -> store failures -> COMPLETED + audit (one commit)

The claim is a single conditional UPDATE, so two concurrent requests cannot both validate the
same batch: the loser gets 409. Everything after the claim is one transaction; if anything
fails it is rolled back (no partial results), the batch is marked FAILED and the failure is
audited. A FAILED batch can be validated again. A COMPLETED batch cannot, because re-running
would delete results that reviewers may already have annotated.

Limitation: if the server process is killed mid-run, the batch stays VALIDATING until it is
reset by hand. There is no automatic recovery in this version.

execute_rules() is pure (DataFrame in, DataFrame out) so it can be tested without a database.
"""
import logging
import time
import uuid
from datetime import date, timezone

import pandas as pd
from sqlalchemy import distinct, func, insert, select, update
from sqlalchemy.orm import Session

from backend.errors import DataGuardError
from backend.models import (AuditLog, Customer, IngestionBatch, Transaction, ValidationResult,
                            ValidationRule)
from backend.services import audit, ingestion, quality_scoring
from backend.services.rules import REGISTRY, Rule, RuleContext
from backend.services.rules.base import ALLOWED_REFERENCES, TRANSACTION_COLUMNS
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

CHUNK_SIZE = 5000
MAX_STORED_VALUE_CHARS = 1000
RESULT_COLUMNS = ["rule_code", "record_id", "row_number", "column_name", "offending_value", "error_description"]
VALIDATABLE_FROM = ("UPLOADED", "FAILED")


# ----------------------------------------------------------------- pure engine

def execute_rules(df: pd.DataFrame, rules: list[Rule], ctx: RuleContext) -> pd.DataFrame:
    """Run every rule over the frame. Returns one row per failure."""
    parts = []
    if len(df):
        for rule in rules:
            mask = rule.evaluate(df, ctx)
            if not isinstance(mask, pd.Series) or mask.dtype != bool or not mask.index.equals(df.index):
                raise RuntimeError(f"Rule {rule.code} returned an invalid mask")
            if not mask.any():
                continue
            failing = df[mask]
            col = rule.column
            values = failing[col].tolist() if col else [None] * len(failing)
            parts.append(pd.DataFrame({
                "rule_code": rule.code,
                "record_id": failing["record_id"].to_numpy(),
                "row_number": failing["source_row_number"].to_numpy(),
                "column_name": col,
                "offending_value": [v[:MAX_STORED_VALUE_CHARS] if isinstance(v, str) else v for v in values],
                "error_description": [rule.describe(v)[:2000] for v in values],
            }))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=RESULT_COLUMNS)


# ----------------------------------------------------------------- rule configuration

def build_rule(row: ValidationRule) -> Rule:
    cls = REGISTRY.get(row.rule_code)
    if cls is None:
        raise DataGuardError("rule_config",
                             f"Rule {row.rule_code} is active but has no implementation.", rule_code=row.rule_code)
    return cls(row.rule_code, row.parameters or {}, row.severity)


def load_active_rules(session: Session) -> list[Rule]:
    rows = session.scalars(select(ValidationRule).where(ValidationRule.is_active.is_(True))
                           .order_by(ValidationRule.rule_id))
    return [build_rule(r) for r in rows]


def list_rules(session: Session) -> list[dict]:
    rows = session.scalars(select(ValidationRule).order_by(ValidationRule.rule_id))
    return [{**{c: getattr(r, c) for c in ("rule_id", "rule_code", "rule_name", "rule_category",
                                           "rule_description", "severity", "parameters", "is_active")},
             "implemented": r.rule_code in REGISTRY} for r in rows]


def update_rule(session: Session, rule_id: int, changes: dict, actor: str = "system") -> dict:
    """Change severity / active flag / parameters. Applies to future validation runs only."""
    rule = session.get(ValidationRule, rule_id)
    if rule is None:
        raise DataGuardError("not_found", f"Rule {rule_id} does not exist.")
    before = {"severity": rule.severity, "is_active": rule.is_active, "parameters": rule.parameters}
    cls = REGISTRY.get(rule.rule_code)
    if "parameters" in changes and changes["parameters"] is not None:
        if cls is None:
            raise DataGuardError("bad_request", f"Rule {rule.rule_code} has no implementation to validate parameters.")
        try:
            cls.build_params(rule.rule_code, changes["parameters"])
        except DataGuardError as exc:
            raise DataGuardError("malformed", exc.message)
        rule.parameters = changes["parameters"]
    if changes.get("severity") is not None:
        rule.severity = changes["severity"]
    if changes.get("is_active") is not None:
        if changes["is_active"] and cls is None:
            raise DataGuardError("malformed", f"Rule {rule.rule_code} has no implementation and cannot be activated.")
        rule.is_active = changes["is_active"]
    after = {"severity": rule.severity, "is_active": rule.is_active, "parameters": rule.parameters}
    audit.log_action(session, "RULE_UPDATE", "SUCCESS", actor=actor,
                     details={"rule_code": rule.rule_code, "before": before, "after": after})
    session.commit()
    return next(r for r in list_rules(session) if r["rule_id"] == rule_id)


# ----------------------------------------------------------------- orchestration

def _reference_loader(session: Session):
    def load(table: str, column: str) -> set[str]:
        if (table, column) not in ALLOWED_REFERENCES:        # defence in depth; rules already check
            raise DataGuardError("rule_config", f"Reference {table}.{column} is not allowed.")
        return set(session.scalars(select(Customer.customer_id)))
    return load


def _load_frame(session: Session, batch_id: uuid.UUID) -> pd.DataFrame:
    cols = [Transaction.record_id, Transaction.source_row_number] + [getattr(Transaction, c) for c in TRANSACTION_COLUMNS]
    rows = session.execute(select(*cols).where(Transaction.batch_id == batch_id)
                           .order_by(Transaction.source_row_number)).all()
    return pd.DataFrame(rows, columns=["record_id", "source_row_number"] + TRANSACTION_COLUMNS, dtype=object)


def _record_failure(session: Session, batch_id, actor, reason: str, kind: str, mark_failed: bool) -> None:
    session.rollback()
    if mark_failed:
        session.execute(update(IngestionBatch).where(IngestionBatch.batch_id == batch_id)
                        .values(processing_status="FAILED"))
    audit.log_action(session, "VALIDATE", "FAILED", batch_id=batch_id, actor=actor,
                     details={"kind": kind, "reason": reason})
    session.commit()


def run_validation(session: Session, batch_id: uuid.UUID, actor: str | None = None,
                   today: date | None = None) -> dict:
    actor = ingestion._clean_actor(actor)
    batch, dataset_name = ingestion.get_batch(session, batch_id)      # 404 if unknown
    ingested_day = batch.ingestion_timestamp.astimezone(timezone.utc).date()

    # --- pre-checks: nothing has changed yet, so a failure is audited but the batch is untouched
    try:
        if not ingestion.DATASET_SPECS[dataset_name]["validated"]:
            raise DataGuardError("bad_request", f"'{dataset_name}' is reference data and is not validated.")
        rules = load_active_rules(session)
        if not rules:
            raise DataGuardError("bad_request", "No validation rules are active.")
        ctx = RuleContext(today=today or ingested_day, reference_loader=_reference_loader(session))
        for rule in rules:
            rule.preflight(ctx)
        claimed = session.execute(
            update(IngestionBatch)
            .where(IngestionBatch.batch_id == batch_id, IngestionBatch.processing_status.in_(VALIDATABLE_FROM))
            .values(processing_status="VALIDATING")).rowcount
        if claimed == 0:
            session.rollback()
            current = ingestion.get_batch(session, batch_id)[0].processing_status
            raise DataGuardError("conflict", f"Batch is {current} and cannot be validated.", status=current)
        session.commit()
    except DataGuardError as exc:
        _record_failure(session, batch_id, actor, exc.message, exc.kind, mark_failed=False)
        raise

    # --- the run itself: one transaction, all or nothing
    started = time.perf_counter()
    try:
        df = _load_frame(session, batch_id)
        failures = execute_rules(df, rules, ctx)

        rule_ids = {r.rule_code: r.rule_id for r in session.scalars(select(ValidationRule))}
        detected_at = utcnow()
        records = [{
            "batch_id": batch_id, "record_id": int(f.record_id), "record_identifier": _identifier(df, f.record_id),
            "row_number": int(f.row_number), "rule_id": rule_ids[f.rule_code], "column_name": f.column_name,
            "offending_value": f.offending_value, "error_description": f.error_description,
            "detected_at": detected_at,
        } for f in failures.itertuples(index=False)] if len(failures) else []
        for start in range(0, len(records), CHUNK_SIZE):
            session.execute(insert(ValidationResult), records[start:start + CHUNK_SIZE])

        # Score inside the same transaction: a batch is never COMPLETED without its scores.
        session.flush()
        quality_scoring.calculate_and_store(session, batch_id, len(df),
                                            quality_scoring.executed_rules_from_rules(rules))

        duration_ms = int((time.perf_counter() - started) * 1000)
        per_rule = failures.groupby("rule_code").size().to_dict() if len(failures) else {}
        session.execute(update(IngestionBatch).where(IngestionBatch.batch_id == batch_id)
                        .values(processing_status="COMPLETED", completed_at=utcnow()))
        audit.log_action(
            session, "VALIDATE", "SUCCESS", batch_id=batch_id, actor=actor,
            rules_executed=[{"rule_code": r.code, "severity": r.severity, "failures": int(per_rule.get(r.code, 0))}
                            for r in rules],
            records_processed=len(df), failure_count=len(records), duration_ms=duration_ms,
            details={"today": str(ctx.today)})
        session.commit()
        logger.info("Validated batch %s: %s records, %s failures, %s ms", batch_id, len(df), len(records), duration_ms)
    except Exception as exc:
        kind = exc.kind if isinstance(exc, DataGuardError) else "internal_error"
        reason = exc.message if isinstance(exc, DataGuardError) else "Unexpected error during validation"
        _record_failure(session, batch_id, actor, reason, kind, mark_failed=True)
        if not isinstance(exc, DataGuardError):
            logger.exception("Validation failed for batch %s", batch_id)
        raise
    return summarize(session, batch_id)


def _identifier(df: pd.DataFrame, record_id) -> str | None:
    """Business identifier (transaction_id) for a record, cached as a lookup on the frame."""
    lookup = df.attrs.get("_ids")
    if lookup is None:
        lookup = dict(zip(df["record_id"], df["transaction_id"]))
        df.attrs["_ids"] = lookup
    return lookup.get(record_id)


# ----------------------------------------------------------------- summary

def summarize(session: Session, batch_id: uuid.UUID) -> dict:
    batch, dataset_name = ingestion.get_batch(session, batch_id)
    rows = session.execute(
        select(ValidationRule.rule_code, ValidationRule.rule_name, ValidationRule.rule_category,
               ValidationRule.severity, func.count(ValidationResult.result_id))
        .join(ValidationResult, ValidationResult.rule_id == ValidationRule.rule_id)
        .where(ValidationResult.batch_id == batch_id)
        .group_by(ValidationRule.rule_code, ValidationRule.rule_name, ValidationRule.rule_category, ValidationRule.severity)
        .order_by(ValidationRule.rule_code)).all()
    invalid = session.scalar(select(func.count(distinct(ValidationResult.record_id)))
                             .where(ValidationResult.batch_id == batch_id)) or 0
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for _, _, cat, sev, n in rows:
        by_category[cat] = by_category.get(cat, 0) + n
        by_severity[sev] = by_severity.get(sev, 0) + n
    metric = quality_scoring.latest_metric(session, batch_id)
    last = session.execute(
        select(AuditLog.duration_ms, AuditLog.rules_executed)
        .where(AuditLog.batch_id == batch_id, AuditLog.action == "VALIDATE",
               AuditLog.execution_status == "SUCCESS")
        .order_by(AuditLog.log_id.desc()).limit(1)).first()
    return {
        "batch_id": batch_id, "dataset_name": dataset_name, "processing_status": batch.processing_status,
        "total_records": batch.total_records, "invalid_records": invalid,
        "valid_records": batch.total_records - invalid if batch.processing_status == "COMPLETED" else None,
        "total_failures": sum(n for *_, n in rows),
        "failures_by_rule": [{"rule_code": c, "rule_name": nm, "category": cat, "severity": sev, "failures": n}
                             for c, nm, cat, sev, n in rows],
        "failures_by_category": by_category, "failures_by_severity": by_severity,
        "quality": quality_scoring.metric_to_dict(metric) if metric else None,
        "duration_ms": last.duration_ms if last else None,
        "rules_executed": last.rules_executed if last else None,
    }
