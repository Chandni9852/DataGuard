"""Unit tests for the validation rules and the pure engine. No database needed."""
import csv
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backend.errors import DataGuardError
from backend.services.rules import REGISTRY, RuleContext
from backend.services.rules.base import TRANSACTION_COLUMNS
from backend.services.validation import execute_rules
from tests.unit.reference_oracle import reference_failures

DATA = Path(__file__).resolve().parents[2] / "data"
TODAY = date(2026, 11, 12)
CUSTOMERS = {"C1", "C2", "C3"}
SEVERITY = {"C01": "HIGH"}


def ctx(today=TODAY, customers=CUSTOMERS):
    return RuleContext(today=today, references={"customers.customer_id": set(customers)})


def frame(rows: list[dict]) -> pd.DataFrame:
    """Rows as the database would hold them: missing cells are None."""
    good = {"transaction_id": "T1", "customer_id": "C1", "transaction_amount": "10.00",
            "transaction_date": "2026-01-01", "merchant": "Shop", "transaction_status": "COMPLETED"}
    full = [{**good, **r} for r in rows]
    df = pd.DataFrame(full, dtype=object)
    df.insert(0, "source_row_number", range(1, len(df) + 1))
    df.insert(0, "record_id", range(100, 100 + len(df)))
    return df.where(df.notna(), None)


def fails(code: str, rows: list[dict], **kw) -> list[int]:
    """1-based row numbers on which the rule fires."""
    rule = REGISTRY[code](code, kw.pop("params", None))
    out = execute_rules(frame(rows), [rule], kw.pop("context", None) or ctx())
    return sorted(out["row_number"].tolist())


# ------------------------------------------------------------ registry

def test_every_seeded_rule_code_has_an_implementation():
    seeded = {"C01", "C02", "C03", "C04", "C05", "C06", "U01", "U02",
              "V01", "V02", "V03", "V04", "K01", "K02", "R01"}
    assert seeded == set(REGISTRY)


def test_rule_categories_match_their_codes():
    expected = {"C": "COMPLETENESS", "U": "UNIQUENESS", "V": "VALIDITY", "K": "CONSISTENCY",
                "R": "REFERENTIAL_INTEGRITY"}
    assert all(cls.category == expected[code[0]] for code, cls in REGISTRY.items())


# ------------------------------------------------------------ completeness

@pytest.mark.parametrize("code,col", [("C01", "transaction_id"), ("C02", "customer_id"),
                                      ("C03", "transaction_amount"), ("C04", "transaction_date"),
                                      ("C05", "transaction_status"), ("C06", "merchant")])
def test_completeness_flags_none_empty_and_whitespace(code, col):
    assert fails(code, [{col: None}, {col: ""}, {col: "   "}, {col: "\t"}, {}]) == [1, 2, 3, 4]


def test_completeness_describes_the_column():
    out = execute_rules(frame([{"merchant": None}]), [REGISTRY["C06"]("C06")], ctx())
    row = out.iloc[0]
    assert row.column_name == "merchant" and "merchant" in row.error_description and row.offending_value is None


# ------------------------------------------------------------ uniqueness

def test_duplicate_transaction_id_flags_later_occurrences_only():
    rows = [{"transaction_id": "A"}, {"transaction_id": "B"}, {"transaction_id": "A"}, {"transaction_id": "A"}]
    assert fails("U01", rows) == [3, 4]


def test_duplicate_check_ignores_missing_ids():
    rows = [{"transaction_id": None}, {"transaction_id": None}, {"transaction_id": ""}, {"transaction_id": ""}]
    assert fails("U01", rows) == []


def test_exact_duplicate_rows_flagged_but_near_duplicates_are_not():
    rows = [{"transaction_id": "A"}, {"transaction_id": "A"}, {"transaction_id": "A", "merchant": "Other"}]
    assert fails("U02", rows) == [2]
    assert fails("U01", rows) == [2, 3]


def test_u02_has_no_column():
    out = execute_rules(frame([{}, {}]), [REGISTRY["U02"]("U02")], ctx())
    assert out.column_name.isna().all() and out.offending_value.isna().all()


# ------------------------------------------------------------ validity

@pytest.mark.parametrize("value,bad", [
    ("10", False), ("10.50", False), ("-3", False), ("007.5", False), (" 12.5 ", False),
    ("abc", True), ("12,50", True), ("$45", True), ("1.2.3", True), ("1e5", True), (".5", True),
    ("5.", True), ("+5", True), ("NaN", True), ("Infinity", True),
])
def test_v01_numeric_format(value, bad):
    assert fails("V01", [{"transaction_amount": value}]) == ([1] if bad else [])


def test_v01_skips_missing_values():
    assert fails("V01", [{"transaction_amount": None}, {"transaction_amount": " "}]) == []


@pytest.mark.parametrize("value,bad", [("0", True), ("0.00", True), ("-0.01", True), ("-5", True),
                                       ("0.01", False), ("1", False)])
def test_v02_amount_must_be_positive(value, bad):
    assert fails("V02", [{"transaction_amount": value}]) == ([1] if bad else [])


def test_v02_ignores_non_numeric_and_missing():
    assert fails("V02", [{"transaction_amount": "abc"}, {"transaction_amount": None}]) == []


def test_v02_respects_configured_bound():
    rows = [{"transaction_amount": "5"}, {"transaction_amount": "15"}]
    assert fails("V02", rows, params={"min_exclusive": 10}) == [1]


@pytest.mark.parametrize("value,bad", [
    ("2026-01-31", False), ("2024-02-29", False), ("1000-01-01", False), ("9999-12-31", False),
    ("2025-02-29", True), ("2025-02-30", True), ("2025-13-01", True), ("2025-00-10", True),
    ("2025-1-5", True), ("20251231", True), ("31/12/2025", True), ("2025/12/31", True),
    ("2025-12-31T00:00", True), ("not a date", True), ("0000-01-01", True),
])
def test_v03_strict_iso_dates_including_extreme_years(value, bad):
    assert fails("V03", [{"transaction_date": value}]) == ([1] if bad else [])


def test_v03_rejects_unsupported_format_param():
    with pytest.raises(DataGuardError) as ei:
        REGISTRY["V03"]("V03", {"format": "%d/%m/%Y"})
    assert ei.value.kind == "rule_config"


@pytest.mark.parametrize("status,bad", [("COMPLETED", False), (" PENDING ", False), ("completed", True),
                                        ("Done", True), ("COMPLETE", True), ("", False), (None, False)])
def test_v04_status_is_case_sensitive(status, bad):
    assert fails("V04", [{"transaction_status": status}]) == ([1] if bad else [])


def test_v04_uses_configured_allowed_list():
    assert fails("V04", [{"transaction_status": "SETTLED"}], params={"allowed": ["SETTLED"]}) == []
    for bad in ({"allowed": []}, {"allowed": "COMPLETED"}, {"allowed": [1, 2]}):
        with pytest.raises(DataGuardError):
            REGISTRY["V04"]("V04", bad)


# ------------------------------------------------------------ consistency

def test_k01_future_dates_use_the_supplied_today():
    rows = [{"transaction_date": "2026-11-12"}, {"transaction_date": "2026-11-13"}, {"transaction_date": "2030-01-01"}]
    assert fails("K01", rows) == [2, 3]
    assert fails("K01", rows, context=ctx(today=date(2031, 1, 1))) == []


def test_k01_ignores_unparseable_and_missing_dates():
    assert fails("K01", [{"transaction_date": "garbage"}, {"transaction_date": None}]) == []


def test_k02_amount_ceiling_is_strictly_greater_than():
    rows = [{"transaction_amount": "1000000"}, {"transaction_amount": "1000000.00"},
            {"transaction_amount": "1000000.01"}, {"transaction_amount": "5000000"}]
    assert fails("K02", rows) == [3, 4]


def test_k02_uses_exact_decimal_arithmetic():
    assert fails("K02", [{"transaction_amount": "1000000.00000000000001"}]) == [1]


def test_k02_ceiling_param_validated():
    with pytest.raises(DataGuardError):
        REGISTRY["K02"]("K02", {"max": "lots"})
    assert fails("K02", [{"transaction_amount": "50"}], params={"max": 10}) == [1]


# ------------------------------------------------------------ referential integrity

def test_r01_orphans_flagged_missing_ids_left_to_completeness():
    rows = [{"customer_id": "C1"}, {"customer_id": "C9"}, {"customer_id": None}, {"customer_id": ""}]
    assert fails("R01", rows) == [2]


def test_r01_lookup_is_exact_not_trimmed():
    assert fails("R01", [{"customer_id": " C1"}, {"customer_id": "c1"}]) == [1, 2]


def test_r01_preflight_refuses_empty_reference_table():
    rule = REGISTRY["R01"]("R01")
    with pytest.raises(DataGuardError) as ei:
        rule.preflight(ctx(customers=set()))
    assert ei.value.kind == "reference_missing"


def test_r01_rejects_non_whitelisted_reference():
    with pytest.raises(DataGuardError) as ei:
        REGISTRY["R01"]("R01", {"reference_table": "audit_logs", "reference_column": "actor"})
    assert ei.value.kind == "rule_config"
    with pytest.raises(DataGuardError):
        REGISTRY["R01"]("R01", {"reference_table": "customers; DROP TABLE x", "reference_column": "customer_id"})


# ------------------------------------------------------------ engine behaviour

def test_engine_on_empty_frame_returns_no_failures():
    empty = frame([])
    rules = [cls(code) for code, cls in REGISTRY.items()]
    out = execute_rules(empty, rules, ctx())
    assert len(out) == 0 and list(out.columns) == ["rule_code", "record_id", "row_number",
                                                   "column_name", "offending_value", "error_description"]


def test_engine_on_all_null_columns_does_not_crash():
    rows = [{c: None for c in TRANSACTION_COLUMNS} for _ in range(3)]
    rules = [cls(code) for code, cls in REGISTRY.items()]
    out = execute_rules(frame(rows), rules, ctx())
    assert set(out.rule_code) == {"C01", "C02", "C03", "C04", "C05", "C06", "U02"}


def test_one_record_can_fail_several_rules():
    rows = [{"transaction_amount": "-5", "customer_id": "C9", "transaction_status": "BOGUS"}]
    rules = [cls(code) for code, cls in REGISTRY.items()]
    out = execute_rules(frame(rows), rules, ctx())
    assert sorted(out.rule_code) == ["R01", "V02", "V04"]


def test_long_offending_values_are_truncated():
    out = execute_rules(frame([{"transaction_amount": "x" * 5000}]), [REGISTRY["V01"]("V01")], ctx())
    assert len(out.iloc[0].offending_value) == 1000


def test_a_rule_returning_a_bad_mask_is_caught():
    class Bad(REGISTRY["C01"]):
        def evaluate(self, df, c):
            return pd.Series([1] * len(df), index=df.index)
    with pytest.raises(RuntimeError):
        execute_rules(frame([{}]), [Bad("C01")], ctx())


# ------------------------------------------------------------ oracle and answer key

def _sample_frame(name: str) -> tuple[pd.DataFrame, list[dict]]:
    with (DATA / name).open(newline="", encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))
    df = pd.DataFrame(csv_rows, dtype=object)
    df = df.where(df != "", None)         # same mapping ingestion applies: empty cell -> NULL
    df.insert(0, "source_row_number", range(1, len(df) + 1))
    df.insert(0, "record_id", range(1, len(df) + 1))
    return df, csv_rows


def _customers() -> set[str]:
    with (DATA / "customers.csv").open(newline="", encoding="utf-8") as f:
        return {r["customer_id"] for r in csv.DictReader(f)}


def _engine_set(df) -> set[tuple[int, str]]:
    rules = [cls(code) for code, cls in sorted(REGISTRY.items())]
    out = execute_rules(df, rules, ctx(customers=_customers()))
    return set(zip(out.row_number.astype(int), out.rule_code))


def test_engine_matches_independent_oracle_on_corrupted_sample():
    df, csv_rows = _sample_frame("sample_transactions.csv")
    assert _engine_set(df) == reference_failures(csv_rows, _customers(), TODAY)


def test_engine_matches_the_generators_answer_key_exactly():
    df, _ = _sample_frame("sample_transactions.csv")
    key = json.loads((DATA / "sample_transactions_answer_key.json").read_text())
    assert _engine_set(df) == {(r["row_number"], e["rule_code"]) for r in key["rows"] for e in r["expected"]}


def test_engine_finds_nothing_in_the_clean_sample():
    df, _ = _sample_frame("sample_transactions_clean.csv")
    assert _engine_set(df) == set()
