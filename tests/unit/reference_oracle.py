"""A deliberately simple, row-by-row reference implementation of the DataGuard rules.

It exists only to cross-check things: the generator's answer key now, and the real
validation engine (Module 2) later. It is intentionally written differently from the
production engine (plain loops, no vectorization) so the two cannot share a bug.

Semantics (the contract the production rules must satisfy):
- A cell is "missing" if it is empty or whitespace-only.
- A rule skips values owned by a completeness rule (missing) so one defect is not
  reported twice.
- U01 flags the 2nd and later occurrences of a non-missing transaction_id.
- U02 flags the 2nd and later occurrences of a row identical across all six columns.
- V01 amount must parse as a plain decimal number; V02 amount must be > 0.
- V03 date must match YYYY-MM-DD exactly and be a real calendar date.
- V04 status must be one of the allowed values, matched case-sensitively.
- K01 date must not be after `today`; K02 amount must not exceed the ceiling.
- R01 customer_id must exist in the customers reference set.
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MANDATORY = ["transaction_id", "customer_id", "transaction_amount",
             "transaction_date", "merchant", "transaction_status"]
COMPLETENESS_CODE = {
    "transaction_id": "C01", "customer_id": "C02", "transaction_amount": "C03",
    "transaction_date": "C04", "merchant": "C06", "transaction_status": "C05",
}
ALLOWED = {"COMPLETED", "PENDING", "FAILED", "REVERSED"}
CEILING = Decimal("1000000")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CATEGORY = {
    "C": "COMPLETENESS", "U": "UNIQUENESS", "V": "VALIDITY",
    "K": "CONSISTENCY", "R": "REFERENTIAL_INTEGRITY",
}


def _missing(v) -> bool:
    return v is None or str(v).strip() == ""


def _parse_amount(v: str):
    s = v.strip()
    if not _NUMBER.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parse_date(v: str):
    s = v.strip()
    if not _DATE.match(s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def reference_failures(rows: list[dict], customer_ids: set[str], today: date) -> set[tuple[int, str]]:
    """Return {(row_number, rule_code)} with 1-based row numbers."""
    failures: set[tuple[int, str]] = set()
    seen_ids: set[str] = set()
    seen_rows: set[tuple] = set()

    for i, r in enumerate(rows, start=1):
        for col in MANDATORY:
            if _missing(r.get(col)):
                failures.add((i, COMPLETENESS_CODE[col]))

        tid = r.get("transaction_id")
        if not _missing(tid):
            if tid in seen_ids:
                failures.add((i, "U01"))
            seen_ids.add(tid)

        key = tuple(r.get(c) for c in MANDATORY)
        if key in seen_rows:
            failures.add((i, "U02"))
        seen_rows.add(key)

        amt = r.get("transaction_amount")
        if not _missing(amt):
            val = _parse_amount(amt)
            if val is None:
                failures.add((i, "V01"))
            else:
                if val <= 0:
                    failures.add((i, "V02"))
                if val > CEILING:
                    failures.add((i, "K02"))

        d = r.get("transaction_date")
        if not _missing(d):
            parsed = _parse_date(d)
            if parsed is None:
                failures.add((i, "V03"))
            elif parsed > today:
                failures.add((i, "K01"))

        st = r.get("transaction_status")
        if not _missing(st) and st.strip() not in ALLOWED:
            failures.add((i, "V04"))

        cid = r.get("customer_id")
        if not _missing(cid) and cid not in customer_ids:
            failures.add((i, "R01"))

    return failures


def reference_scores(rows: list[dict], failures: set[tuple[int, str]]) -> dict:
    """Quality scores under the DataGuard methodology, from a failure set."""
    n = len(rows)
    if n == 0:
        return {k: None for k in ("completeness", "uniqueness", "validity", "consistency", "overall")}
    missing_cells = sum(1 for _, c in failures if c.startswith("C"))
    uniq = {i for i, c in failures if c.startswith("U")}
    valid = {i for i, c in failures if c.startswith("V")}
    cons = {i for i, c in failures if c[0] in ("K", "R")}
    pct = lambda a, b: round(100.0 * a / b, 2)  # noqa: E731
    out = {
        "completeness": pct(n * len(MANDATORY) - missing_cells, n * len(MANDATORY)),
        "uniqueness": pct(n - len(uniq), n),
        "validity": pct(n - len(valid), n),
        "consistency": pct(n - len(cons), n),
    }
    out["overall"] = round(sum(out.values()) / 4, 2)
    return out
