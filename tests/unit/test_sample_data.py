"""Verifies the sample data generator against an independently written oracle."""
import csv
import importlib.util
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from tests.unit.reference_oracle import reference_failures, reference_scores

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 11, 12)  # any date after the generator's reference date works


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_sample_data", ROOT / "data" / "generate_sample_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load_generator()


def _read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("sample")
    key = gen.generate(rows=1000, seed=42, out_dir=out)
    customers = {r["customer_id"] for r in _read(out / "customers.csv")}
    return out, key, customers


def test_generation_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    gen.generate(500, 7, a)
    gen.generate(500, 7, b)
    for name in ("customers.csv", "sample_transactions_clean.csv", "sample_transactions.csv",
                 "sample_transactions_answer_key.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_different_seed_changes_data(tmp_path):
    gen.generate(500, 1, tmp_path / "a")
    gen.generate(500, 2, tmp_path / "b")
    assert (tmp_path / "a/sample_transactions.csv").read_bytes() != (tmp_path / "b/sample_transactions.csv").read_bytes()


def test_too_few_rows_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        gen.generate(100, 1, tmp_path)


def test_clean_file_has_zero_failures(generated):
    out, _, customers = generated
    rows = _read(out / "sample_transactions_clean.csv")
    assert len(rows) == 1000
    assert reference_failures(rows, customers, TODAY) == set()


def test_answer_key_matches_independent_oracle_row_by_row(generated):
    out, key, customers = generated
    rows = _read(out / "sample_transactions.csv")
    oracle = reference_failures(rows, customers, TODAY)
    from_key = {(r["row_number"], e["rule_code"]) for r in key["rows"] for e in r["expected"]}
    assert oracle == from_key


def test_every_required_defect_type_is_present(generated):
    """The seven defect types required by the project brief."""
    _, key, _ = generated
    by_rule = key["expected_failures_by_rule"]
    required = {
        "missing customer IDs": "C02", "duplicate transaction IDs": "U01",
        "negative amounts": "V02", "invalid date formats": "V03",
        "invalid statuses": "V04", "missing merchant names": "C06",
        "referential integrity violations": "R01",
    }
    for label, code in required.items():
        assert by_rule.get(code, 0) > 0, f"no rows injected for {label} ({code})"


def test_every_seeded_rule_is_exercised(generated):
    _, key, _ = generated
    assert set(key["expected_failures_by_rule"]) == set(gen.RULE_CATEGORY)


def test_aggregates_in_key_are_consistent(generated):
    _, key, _ = generated
    assert key["total_failures"] == sum(key["expected_failures_by_rule"].values())
    assert key["total_failures"] == sum(len(r["expected"]) for r in key["rows"])
    assert key["invalid_records"] == len(key["rows"])
    assert key["valid_records"] + key["invalid_records"] == key["total_records"]
    assert sum(key["expected_failures_by_category"].values()) == key["total_failures"]


def test_expected_scores_match_oracle_and_are_in_range(generated):
    out, key, customers = generated
    rows = _read(out / "sample_transactions.csv")
    scores = reference_scores(rows, reference_failures(rows, customers, TODAY))
    assert scores == key["expected_scores"]
    assert all(0 <= v <= 100 for v in scores.values())
    assert scores["overall"] == round(sum(scores[k] for k in
              ("completeness", "uniqueness", "validity", "consistency")) / 4, 2)


def test_multi_defect_rows_count_once_per_dimension(generated):
    """A row failing V02 + V04 + R01 must reduce validity and consistency by one record each."""
    out, key, customers = generated
    multi = [r for r in key["rows"] if len(r["expected"]) == 3]
    assert len(multi) == 2
    validity_records = {r["row_number"] for r in key["rows"]
                        if any(e["rule_code"].startswith("V") for e in r["expected"])}
    failures_validity = sum(1 for r in key["rows"] for e in r["expected"] if e["rule_code"].startswith("V"))
    assert key["records_failing_validity"] == len(validity_records)
    assert failures_validity > len(validity_records)  # proves multiple failures were collapsed


def test_unmodified_safe_rows_stay_valid_and_unique(generated):
    out, key, _ = generated
    flagged = {r["row_number"] for r in key["rows"]}
    assert not any(n <= gen.SAFE_ROWS for n in flagged)
    ids = [r["transaction_id"] for r in _read(out / "sample_transactions_clean.csv")]
    assert len(set(ids)) == len(ids)


def test_exact_duplicate_rows_are_flagged_by_both_uniqueness_rules(generated):
    _, key, _ = generated
    both = [r for r in key["rows"]
            if {"U01", "U02"} <= {e["rule_code"] for e in r["expected"]}]
    assert len(both) == 5


def test_files_have_expected_shape(generated):
    out, _, _ = generated
    for name in ("sample_transactions.csv", "sample_transactions_clean.csv"):
        with (out / name).open(newline="", encoding="utf-8") as f:
            assert next(csv.reader(f)) == gen.COLUMNS
    assert len(_read(out / "customers.csv")) == 200
    assert Counter(len(r) for r in _read(out / "sample_transactions.csv")) == {6: 1000}


def test_committed_sample_files_are_up_to_date(tmp_path):
    """The files checked into data/ must equal a fresh default generation (no drift)."""
    data_dir = ROOT / "data"
    if not (data_dir / "sample_transactions.csv").exists():
        pytest.skip("sample files not generated yet")
    gen.generate(1000, 42, tmp_path)
    for name in ("customers.csv", "sample_transactions_clean.csv", "sample_transactions.csv",
                 "sample_transactions_answer_key.json"):
        assert (data_dir / name).read_bytes() == (tmp_path / name).read_bytes(), f"{name} is stale"
