"""Reproducible synthetic data for DataGuard. No real customer or financial data.

Writes into the output directory:
  customers.csv                       reference data for the referential-integrity rule
  sample_transactions_clean.csv       every row satisfies every rule
  sample_transactions.csv             same shape, with intentionally injected defects
  sample_transactions_answer_key.json exactly which rows violate which rules, plus the
                                      expected quality scores under the DataGuard methodology

Usage:  python data/generate_sample_data.py [--rows 1000] [--seed 42] [--out data]

Design notes
- Defects are injected only into rows 101..N. Rows 1..100 are never modified and serve as the
  "first occurrence" that duplicates copy from, so a duplicate always comes after its source.
- Each injected defect gets its own row, except two deliberate multi-defect rows that test
  that a record failing several rules is counted once per dimension (no double counting).
- The answer key is the independent oracle the ingestion, validation and scoring tests use.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

COLUMNS = ["transaction_id", "customer_id", "transaction_amount",
           "transaction_date", "merchant", "transaction_status"]
MANDATORY = list(COLUMNS)  # all six; merchant is lower severity but still mandatory
ALLOWED_STATUSES = ["COMPLETED", "PENDING", "FAILED", "REVERSED"]
MERCHANTS = ["Northwind Grocers", "Blue Harbor Fuel", "Lumen Electronics", "Cedar & Pine Cafe",
             "Atlas Travel Co", "Pioneer Pharmacy", "Summit Outfitters", "Harvest Table",
             "Orbit Streaming", "Metro Transit Pass", "Willow Home Goods", "Keystone Hardware"]
FIRST = ["Ava", "Liam", "Noah", "Mia", "Ethan", "Zoe", "Lucas", "Ivy", "Owen", "Nora",
         "Arjun", "Meera", "Kenji", "Sofia", "Omar", "Leila", "Mateo", "Hana", "Tariq", "Elena"]
LAST = ["Stone", "Rivera", "Patel", "Nguyen", "Kim", "Okafor", "Silva", "Novak", "Haddad",
        "Larsen", "Mehta", "Rossi", "Tanaka", "Ibrahim", "Moreau", "Fischer", "Cohen", "Reyes"]

# Fixed reference date so output is deterministic. All clean dates fall on or before it,
# which is in the past for any realistic ingestion date.
REF_DATE = date(2026, 6, 30)
AMOUNT_CEILING = 1_000_000  # must match the K02 seed parameter

RULE_CATEGORY = {
    "C01": "COMPLETENESS", "C02": "COMPLETENESS", "C03": "COMPLETENESS",
    "C04": "COMPLETENESS", "C05": "COMPLETENESS", "C06": "COMPLETENESS",
    "U01": "UNIQUENESS", "U02": "UNIQUENESS",
    "V01": "VALIDITY", "V02": "VALIDITY", "V03": "VALIDITY", "V04": "VALIDITY",
    "K01": "CONSISTENCY", "K02": "CONSISTENCY",
    "R01": "REFERENTIAL_INTEGRITY",
}
COMPLETENESS_COLUMN = {
    "C01": "transaction_id", "C02": "customer_id", "C03": "transaction_amount",
    "C04": "transaction_date", "C05": "transaction_status", "C06": "merchant",
}

SAFE_ROWS = 100  # rows never modified; source pool for duplicates


def _customers(n: int, rng: random.Random) -> list[dict]:
    return [{"customer_id": f"C{i:04d}",
             "customer_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}"}
            for i in range(1, n + 1)]


def _clean_rows(n: int, customers: list[dict], rng: random.Random) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "transaction_id": f"T{i:06d}",
            "customer_id": rng.choice(customers)["customer_id"],
            "transaction_amount": f"{rng.uniform(5, 5000):.2f}",
            "transaction_date": (REF_DATE - timedelta(days=rng.randint(0, 364))).isoformat(),
            "merchant": rng.choice(MERCHANTS),
            "transaction_status": rng.choice(ALLOWED_STATUSES),
        })
    return rows


def _inject(rows: list[dict], rng: random.Random, customer_ids: set[str]) -> dict[int, list[tuple[str, str]]]:
    """Mutate rows in place. Returns {row_index: [(rule_code, column), ...]}."""
    n = len(rows)
    pool = list(range(SAFE_ROWS, n))
    rng.shuffle(pool)
    expected: dict[int, list[tuple[str, str]]] = {}

    def take(k: int) -> list[int]:
        picked, rest = pool[:k], pool[k:]
        pool[:] = rest
        if len(picked) < k:
            raise ValueError(f"Not enough rows: need {k} more defect rows; increase --rows")
        return picked

    def record(idx: int, code: str, column: str) -> None:
        expected.setdefault(idx, []).append((code, column))

    # Completeness -----------------------------------------------------------
    for idx in take(3):
        rows[idx]["transaction_id"] = ""
        record(idx, "C01", "transaction_id")
    for idx in take(10):                              # missing customer IDs
        rows[idx]["customer_id"] = ""
        record(idx, "C02", "customer_id")
    for idx in take(3):
        rows[idx]["transaction_amount"] = ""
        record(idx, "C03", "transaction_amount")
    for idx in take(3):
        rows[idx]["transaction_date"] = ""
        record(idx, "C04", "transaction_date")
    for idx in take(3):
        rows[idx]["transaction_status"] = ""
        record(idx, "C05", "transaction_status")
    for k, idx in enumerate(take(10)):                # missing merchant; some whitespace-only
        rows[idx]["merchant"] = "   " if k < 2 else ""
        record(idx, "C06", "merchant")

    # Uniqueness -------------------------------------------------------------
    for idx in take(8):                               # duplicate transaction ID only
        rows[idx]["transaction_id"] = rows[rng.randrange(SAFE_ROWS)]["transaction_id"]
        record(idx, "U01", "transaction_id")
    for idx in take(5):                               # fully identical rows (also share the ID)
        rows[idx] = dict(rows[rng.randrange(SAFE_ROWS)])
        record(idx, "U01", "transaction_id")
        record(idx, "U02", "")

    # Validity ---------------------------------------------------------------
    for idx in take(10):                              # negative amounts
        rows[idx]["transaction_amount"] = f"-{rng.uniform(1, 900):.2f}"
        record(idx, "V02", "transaction_amount")
    for idx in take(3):                               # zero amounts
        rows[idx]["transaction_amount"] = "0.00"
        record(idx, "V02", "transaction_amount")
    for k, idx in enumerate(take(5)):                 # non-numeric amounts
        rows[idx]["transaction_amount"] = ["abc", "12,50x", "N/A", "$45", "1.2.3"][k]
        record(idx, "V01", "transaction_amount")
    for k, idx in enumerate(take(8)):                 # invalid date formats / impossible dates
        rows[idx]["transaction_date"] = ["31/12/2025", "2025-13-45", "12-31-2025", "2025/12/31",
                                         "not a date", "2025-02-30", "20251231", "2025-1-5"][k]
        record(idx, "V03", "transaction_date")
    for k, idx in enumerate(take(8)):                 # invalid statuses (case-sensitive match)
        rows[idx]["transaction_status"] = ["COMPLETE", "UNKNOWN", "Done", "completed",
                                           "SETTLED", "CANCELLED", "OK", "PEND"][k]
        record(idx, "V04", "transaction_status")

    # Consistency ------------------------------------------------------------
    for idx in take(5):                               # dates in the future
        rows[idx]["transaction_date"] = (date(2030, 1, 1) + timedelta(days=rng.randint(0, 300))).isoformat()
        record(idx, "K01", "transaction_date")
    for idx in take(3):                               # above amount ceiling
        rows[idx]["transaction_amount"] = f"{AMOUNT_CEILING + rng.randint(1, 1_500_000):.2f}"
        record(idx, "K02", "transaction_amount")

    # Referential integrity --------------------------------------------------
    for k, idx in enumerate(take(10)):                # orphan customer IDs
        orphan = f"C{9000 + k:04d}"
        assert orphan not in customer_ids
        rows[idx]["customer_id"] = orphan
        record(idx, "R01", "customer_id")

    # Deliberate multi-defect rows (must count once per dimension) -----------
    for idx in take(2):
        rows[idx]["transaction_amount"] = f"-{rng.uniform(1, 900):.2f}"
        rows[idx]["customer_id"] = "C9999"
        rows[idx]["transaction_status"] = "BOGUS"
        record(idx, "V02", "transaction_amount")
        record(idx, "R01", "customer_id")
        record(idx, "V04", "transaction_status")

    return expected


def _expected_summary(n: int, rows: list[dict], expected: dict[int, list[tuple[str, str]]]) -> dict:
    by_rule: dict[str, int] = {}
    by_category: dict[str, int] = {}
    affected = {"UNIQUENESS": set(), "VALIDITY": set(), "CONSISTENCY": set()}
    missing_cells = 0
    for idx, items in expected.items():
        for code, _ in items:
            by_rule[code] = by_rule.get(code, 0) + 1
            cat = RULE_CATEGORY[code]
            by_category[cat] = by_category.get(cat, 0) + 1
            if cat == "COMPLETENESS":
                missing_cells += 1
            elif cat in ("UNIQUENESS", "VALIDITY"):
                affected[cat].add(idx)
            else:  # CONSISTENCY and REFERENTIAL_INTEGRITY both feed the consistency score
                affected["CONSISTENCY"].add(idx)

    def pct(num: int, den: int) -> float:
        return round(100.0 * num / den, 2)

    completeness = pct(n * len(MANDATORY) - missing_cells, n * len(MANDATORY))
    uniqueness = pct(n - len(affected["UNIQUENESS"]), n)
    validity = pct(n - len(affected["VALIDITY"]), n)
    consistency = pct(n - len(affected["CONSISTENCY"]), n)
    overall = round((completeness + uniqueness + validity + consistency) / 4, 2)
    return {
        "expected_failures_by_rule": dict(sorted(by_rule.items())),
        "expected_failures_by_category": dict(sorted(by_category.items())),
        "total_failures": sum(by_rule.values()),
        "invalid_records": len(expected),
        "valid_records": n - len(expected),
        "missing_mandatory_cells": missing_cells,
        "records_failing_uniqueness": len(affected["UNIQUENESS"]),
        "records_failing_validity": len(affected["VALIDITY"]),
        "records_failing_consistency_or_referential": len(affected["CONSISTENCY"]),
        "expected_scores": {
            "completeness": completeness, "uniqueness": uniqueness,
            "validity": validity, "consistency": consistency, "overall": overall,
        },
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def generate(rows: int = 1000, seed: int = 42, out_dir: str | Path = "data") -> dict:
    """Write all sample files into out_dir and return the answer key."""
    if rows < 300:
        raise ValueError("rows must be at least 300 so every defect type fits outside the safe rows")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    customers = _customers(200, rng)
    customer_ids = {c["customer_id"] for c in customers}
    clean = _clean_rows(rows, customers, rng)
    dirty = [dict(r) for r in clean]
    expected = _inject(dirty, rng, customer_ids)

    _write_csv(out / "customers.csv", ["customer_id", "customer_name"], customers)
    _write_csv(out / "sample_transactions_clean.csv", COLUMNS, clean)
    _write_csv(out / "sample_transactions.csv", COLUMNS, dirty)

    key = {
        "seed": seed,
        "total_records": rows,
        "mandatory_columns": MANDATORY,
        "reference_date": REF_DATE.isoformat(),
        "methodology": {
            "completeness": "non-blank mandatory cells / (records x mandatory columns)",
            "uniqueness": "records not flagged U01/U02 / total records",
            "validity": "records with no VALIDITY failure / total records",
            "consistency": "records with no CONSISTENCY or REFERENTIAL_INTEGRITY failure / total records",
            "overall": "equal-weighted mean of the four",
        },
        **_expected_summary(rows, dirty, expected),
        "rows": [
            {"row_number": idx + 1,
             "transaction_id": dirty[idx]["transaction_id"],
             "expected": [{"rule_code": c, "column": col} for c, col in sorted(items)]}
            for idx, items in sorted(expected.items())
        ],
    }
    (out / "sample_transactions_answer_key.json").write_text(
        json.dumps(key, indent=2) + "\n", encoding="utf-8")
    return key


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent))
    args = ap.parse_args()
    k = generate(args.rows, args.seed, args.out)
    print(f"Wrote files to {args.out}: {k['total_records']} rows, "
          f"{k['invalid_records']} invalid, {k['total_failures']} failures, "
          f"expected overall score {k['expected_scores']['overall']}")
