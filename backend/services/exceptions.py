"""Module 4: exception management.

An "exception" is one stored validation failure. Reviewing never edits or deletes source
records or failure rows. Every review decision is a new row in exception_reviews
(append-only), and the *current* status of an exception is its most recent review row, or
OPEN when it has none. That gives the review history for free and makes every decision
traceable to a reviewer and a time.

Governance rules enforced here:
- A reviewer name is required (decisions must be attributable).
- Waiving an exception, or re-opening it, requires a comment explaining why.
- Setting the status an exception already has is refused unless a comment is added
  (so the history only records real changes or real notes).
"""
import csv
import io
import re
import uuid
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from backend.errors import DataGuardError
from backend.models import (ExceptionReview, IngestionBatch, Transaction, ValidationResult, ValidationRule)
from backend.services import audit, ingestion
from backend.services.rules.base import TRANSACTION_COLUMNS

CATEGORIES = ("COMPLETENESS", "UNIQUENESS", "VALIDITY", "CONSISTENCY", "REFERENTIAL_INTEGRITY")
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
STATUSES = ("OPEN", "IN_REVIEW", "REVIEWED", "WAIVED")
COMMENT_REQUIRED_FOR = ("WAIVED", "OPEN")
MAX_COMMENT_CHARS = 2000
MAX_BULK = 500
CSV_CHUNK = 2000

V, R, B = ValidationResult, ValidationRule, IngestionBatch
LR = aliased(ExceptionReview)                      # the latest review of each exception
_latest_ids = (select(ExceptionReview.result_id.label("result_id"),
                      func.max(ExceptionReview.review_id).label("rid"))
               .group_by(ExceptionReview.result_id).subquery("latest_review_ids"))
STATUS = func.coalesce(LR.review_status, "OPEN")
SEVERITY_RANK = case({"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4}, value=R.severity, else_=5)

SORTS = {
    "severity": (SEVERITY_RANK, B.ingestion_timestamp.desc(), V.row_number, V.result_id),
    "detected": (V.detected_at.desc(), V.result_id),
    "row": (V.row_number, V.result_id),
    "rule": (R.rule_code, V.row_number, V.result_id),
}

LIST_COLUMNS = (
    V.result_id, V.batch_id, B.source_file, V.record_id, V.record_identifier, V.row_number,
    R.rule_code, R.rule_name, R.rule_category.label("category"), R.severity, V.column_name,
    V.offending_value, V.error_description, V.detected_at, STATUS.label("review_status"),
    LR.reviewer, LR.reviewed_at, LR.comments.label("last_comment"),
)


@dataclass
class Filters:
    batch_id: uuid.UUID | None = None
    category: str | None = None
    severity: str | None = None
    rule_code: str | None = None
    review_status: str | None = None
    q: str | None = None                  # record identifier contains (case-insensitive)


# ----------------------------------------------------------------- pure helpers

def escape_like(value: str) -> str:
    """Escape LIKE wildcards so user text is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_PLAIN_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def csv_safe(value) -> str:
    """Neutralise spreadsheet formula injection in an exported cell.

    A text cell starting with = + - @ (or a tab / carriage return) can run as a formula when the
    file is opened in Excel. Such cells are prefixed with an apostrophe. A plain number such as
    -5.00 cannot be a formula, so it is left untouched; the evidence must stay exact.
    """
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in "=+-@\t\r" and not _PLAIN_NUMBER.match(s):
        return "'" + s
    return s


# ----------------------------------------------------------------- query building

def _base(columns, f: Filters):
    q = (select(*columns).select_from(V)
         .join(R, R.rule_id == V.rule_id).join(B, B.batch_id == V.batch_id)
         .outerjoin(_latest_ids, _latest_ids.c.result_id == V.result_id)
         .outerjoin(LR, LR.review_id == _latest_ids.c.rid))
    if f.batch_id:
        q = q.where(V.batch_id == f.batch_id)
    if f.category:
        q = q.where(R.rule_category == f.category)
    if f.severity:
        q = q.where(R.severity == f.severity)
    if f.rule_code:
        q = q.where(R.rule_code == f.rule_code)
    if f.review_status:
        q = q.where(STATUS == f.review_status)
    if f.q:
        q = q.where(V.record_identifier.ilike(f"%{escape_like(f.q.strip())}%", escape="\\"))
    return q


def check_filters(session: Session, f: Filters) -> None:
    if f.batch_id:
        ingestion.get_batch(session, f.batch_id)          # 404 for an unknown batch


def list_exceptions(session: Session, f: Filters, sort: str, limit: int, offset: int) -> tuple[list[dict], int]:
    check_filters(session, f)
    total = session.scalar(select(func.count()).select_from(_base((V.result_id,), f).subquery())) or 0
    rows = session.execute(_base(LIST_COLUMNS, f).order_by(*SORTS[sort]).limit(limit).offset(offset)).all()
    return [dict(r._mapping) for r in rows], total


def summary(session: Session, f: Filters) -> dict:
    check_filters(session, f)
    sub = _base((R.rule_category.label("category"), R.severity.label("severity"),
                 STATUS.label("status")), f).subquery()

    def counts(col, keys):
        found = dict(session.execute(select(col, func.count()).select_from(sub).group_by(col)).all())
        return {k: int(found.get(k, 0)) for k in keys}

    by_status = counts(sub.c.status, STATUSES)
    return {"total": sum(by_status.values()), "by_review_status": by_status,
            "by_category": counts(sub.c.category, CATEGORIES), "by_severity": counts(sub.c.severity, SEVERITIES)}


def _history(session: Session, result_id: int) -> list[dict]:
    rows = session.scalars(select(ExceptionReview).where(ExceptionReview.result_id == result_id)
                           .order_by(ExceptionReview.review_id.desc()))
    return [{c: getattr(r, c) for c in ("review_id", "result_id", "review_status", "reviewer", "comments", "reviewed_at")}
            for r in rows]


def get_exception(session: Session, result_id: int) -> dict:
    row = session.execute(_base(LIST_COLUMNS, Filters()).where(V.result_id == result_id)).first()
    if row is None:
        raise DataGuardError("not_found", f"Exception {result_id} does not exist.")
    d = dict(row._mapping)
    t = session.get(Transaction, d["record_id"])
    d["record"] = {"source_row_number": t.source_row_number, **{c: getattr(t, c) for c in TRANSACTION_COLUMNS}}
    d["history"] = _history(session, result_id)
    return d


def get_history(session: Session, result_id: int) -> list[dict]:
    if session.get(V, result_id) is None:
        raise DataGuardError("not_found", f"Exception {result_id} does not exist.")
    return _history(session, result_id)


# ----------------------------------------------------------------- review workflow

def _clean_reviewer(reviewer: str | None) -> str:
    name = (reviewer or "").strip()
    if not name:
        raise DataGuardError("malformed", "A reviewer name is required so the decision can be attributed.")
    return ingestion._clean_actor(name)


def _clean_comment(comments: str | None) -> str | None:
    c = (comments or "").strip()
    if len(c) > MAX_COMMENT_CHARS:
        raise DataGuardError("malformed", f"Comments are limited to {MAX_COMMENT_CHARS} characters.")
    return c or None


def _current_statuses(session: Session, ids: list[int]) -> dict[int, str]:
    rows = session.execute(select(V.result_id, STATUS).select_from(V)
                           .outerjoin(_latest_ids, _latest_ids.c.result_id == V.result_id)
                           .outerjoin(LR, LR.review_id == _latest_ids.c.rid)
                           .where(V.result_id.in_(ids))).all()
    return {rid: st for rid, st in rows}


def _apply(session: Session, ids: list[int], status: str, reviewer: str | None, comments: str | None,
           skip_unchanged: bool) -> dict:
    reviewer = _clean_reviewer(reviewer)
    comment = _clean_comment(comments)
    if status in COMMENT_REQUIRED_FOR and not comment:
        raise DataGuardError("malformed", f"A comment explaining the decision is required for status {status}.")

    # Lock the exceptions (in id order, so concurrent reviewers cannot deadlock) before reading
    # their current status; otherwise two reviewers could both act on the same stale status.
    locked = session.execute(select(V.result_id, V.batch_id, R.rule_code).join(R, R.rule_id == V.rule_id)
                             .where(V.result_id.in_(ids)).order_by(V.result_id).with_for_update(of=V)).all()
    found = {r.result_id: r for r in locked}
    missing = [i for i in ids if i not in found]
    if missing:
        raise DataGuardError("not_found", f"{len(missing)} exception(s) do not exist.", missing=missing[:20])

    current = _current_statuses(session, ids)
    changes, skipped = [], []
    for i in ids:
        if current[i] == status and not comment:
            if skip_unchanged:
                skipped.append(i)
                continue
            raise DataGuardError("conflict", f"Exception {i} is already {status}. Add a comment to record a note.")
        changes.append(i)

    for i in changes:
        session.add(ExceptionReview(result_id=i, review_status=status, reviewer=reviewer, comments=comment))
    if changes:
        batches = sorted({str(found[i].batch_id) for i in changes})
        audit.log_action(
            session, "EXCEPTION_REVIEW", "SUCCESS", actor=reviewer, records_processed=len(changes),
            batch_id=found[changes[0]].batch_id if len(batches) == 1 else None,
            details={"new_status": status, "count": len(changes), "skipped_unchanged": len(skipped),
                     "result_ids": changes[:MAX_BULK], "previous_statuses": sorted({current[i] for i in changes}),
                     "has_comment": comment is not None, "batches": batches[:20]})
    session.commit()
    return {"updated": len(changes), "skipped_unchanged": len(skipped), "result_ids": changes}


def review(session: Session, result_id: int, status: str, reviewer: str | None, comments: str | None) -> dict:
    _apply(session, [result_id], status, reviewer, comments, skip_unchanged=False)
    return get_exception(session, result_id)


def review_bulk(session: Session, result_ids: list[int], status: str, reviewer: str | None,
                comments: str | None) -> dict:
    ids = sorted(set(result_ids))
    if not ids or len(ids) > MAX_BULK:
        raise DataGuardError("malformed", f"Provide between 1 and {MAX_BULK} distinct exception ids.")
    return _apply(session, ids, status, reviewer, comments, skip_unchanged=True)


# ----------------------------------------------------------------- CSV export

CSV_HEADER = ["result_id", "batch_id", "source_file", "row_number", "record_identifier", "rule_code", "rule_name",
              "category", "severity", "column_name", "offending_value", "error_description", "detected_at",
              "review_status", "reviewer", "reviewed_at", "last_comment"]


def csv_chunks(session: Session, f: Filters) -> Iterator[str]:
    """Yield the CSV in chunks so large exports are never held in memory. Same order as the list view."""
    def emit(rows) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        for r in rows:
            m = r._mapping
            w.writerow([csv_safe(m["result_id"]), csv_safe(m["batch_id"]), csv_safe(m["source_file"]),
                        csv_safe(m["row_number"]), csv_safe(m["record_identifier"]), csv_safe(m["rule_code"]),
                        csv_safe(m["rule_name"]), csv_safe(m["category"]), csv_safe(m["severity"]),
                        csv_safe(m["column_name"]), csv_safe(m["offending_value"]), csv_safe(m["error_description"]),
                        csv_safe(m["detected_at"].isoformat()), csv_safe(m["review_status"]), csv_safe(m["reviewer"]),
                        csv_safe(m["reviewed_at"].isoformat() if m["reviewed_at"] else None),
                        csv_safe(m["last_comment"])])
        return buf.getvalue()

    header = io.StringIO()
    csv.writer(header, lineterminator="\n").writerow(CSV_HEADER)
    yield header.getvalue()
    q = _base(LIST_COLUMNS, f).order_by(*SORTS["severity"]).execution_options(yield_per=CSV_CHUNK)
    for part in session.execute(q).partitions():
        yield emit(part)
