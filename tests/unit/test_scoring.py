"""Unit tests for the scoring methodology. Pure functions, no database."""
import json
import random
from decimal import Decimal
from pathlib import Path

import pytest

from backend.services.quality_scoring import ScoreInputs, compute_scores

DATA = Path(__file__).resolve().parents[2] / "data"


def inputs(**kw) -> ScoreInputs:
    base = dict(total_records=100, invalid_records=0, completeness_columns=6, missing_cells=0,
                uniqueness_assessed=True, validity_assessed=True, consistency_assessed=True)
    return ScoreInputs(**{**base, **kw})


def test_perfect_data_scores_100_everywhere():
    s = compute_scores(inputs())
    assert [s[k] for k in ("completeness", "uniqueness", "validity", "consistency", "overall")] == [Decimal("100.00")] * 5
    assert s["valid_records"] == 100 and s["invalid_records"] == 0


def test_formulas_for_each_dimension():
    s = compute_scores(inputs(total_records=200, invalid_records=40, missing_cells=60,
                              uniqueness_failed_records=10, validity_failed_records=20, consistency_failed_records=30))
    assert s["completeness"] == Decimal("95.00")      # (1200-60)/1200
    assert s["uniqueness"] == Decimal("95.00")        # (200-10)/200
    assert s["validity"] == Decimal("90.00")
    assert s["consistency"] == Decimal("85.00")
    assert s["overall"] == Decimal("91.25")           # (95+95+90+85)/4
    assert s["valid_records"] == 160


def test_matches_the_generators_answer_key():
    key = json.loads((DATA / "sample_transactions_answer_key.json").read_text())
    s = compute_scores(ScoreInputs(
        total_records=key["total_records"], invalid_records=key["invalid_records"],
        completeness_columns=len(key["mandatory_columns"]), missing_cells=key["missing_mandatory_cells"],
        uniqueness_assessed=True, uniqueness_failed_records=key["records_failing_uniqueness"],
        validity_assessed=True, validity_failed_records=key["records_failing_validity"],
        consistency_assessed=True, consistency_failed_records=key["records_failing_consistency_or_referential"]))
    expected = key["expected_scores"]
    assert {k: float(s[k]) for k in expected} == expected
    assert s["valid_records"] == key["valid_records"]


def test_overall_equals_the_mean_of_the_displayed_scores():
    rng = random.Random(7)
    for _ in range(500):
        n = rng.randint(1, 5000)
        s = compute_scores(inputs(
            total_records=n, completeness_columns=rng.randint(1, 8), missing_cells=rng.randint(0, n),
            uniqueness_failed_records=rng.randint(0, n), validity_failed_records=rng.randint(0, n),
            consistency_failed_records=rng.randint(0, n), invalid_records=rng.randint(0, n)))
        shown = [s[d] for d in ("completeness", "uniqueness", "validity", "consistency")]
        assert all(Decimal(0) <= v <= Decimal(100) for v in shown + [s["overall"]])
        mean = sum(shown) / 4
        assert abs(s["overall"] - mean) <= Decimal("0.005")


# ------------------------------------------------------------------ edge cases

def test_empty_dataset_has_no_scores_not_a_perfect_one():
    s = compute_scores(inputs(total_records=0))
    assert all(s[k] is None for k in ("completeness", "uniqueness", "validity", "consistency", "overall"))
    assert s["valid_records"] == 0 and s["invalid_records"] == 0


def test_a_record_failing_five_validity_rules_counts_once():
    """The caller counts distinct records per dimension, so 1 bad record = 1 failed record."""
    s = compute_scores(inputs(total_records=10, validity_failed_records=1, invalid_records=1))
    assert s["validity"] == Decimal("90.00") and s["valid_records"] == 9


def test_one_record_can_lower_several_dimensions_but_is_one_invalid_record():
    s = compute_scores(inputs(total_records=10, validity_failed_records=1, consistency_failed_records=1,
                              uniqueness_failed_records=1, invalid_records=1))
    assert (s["uniqueness"], s["validity"], s["consistency"]) == (Decimal("90.00"),) * 3
    assert s["valid_records"] == 9


def test_missing_cells_cannot_push_completeness_below_zero():
    assert compute_scores(inputs(total_records=1, completeness_columns=2, missing_cells=99))["completeness"] == Decimal("0.00")


def test_unassessed_overall_is_the_mean_of_the_three_assessed():
    s = compute_scores(inputs(uniqueness_assessed=False, validity_failed_records=10, consistency_failed_records=20))
    assert s["uniqueness"] is None                    # not assessed, so not a misleading 100
    assert s["overall"] == Decimal("90.00")           # (100 + 90 + 80) / 3


def test_no_completeness_rules_means_completeness_is_not_assessed():
    s = compute_scores(inputs(completeness_columns=0))
    assert s["completeness"] is None and s["overall"] == Decimal("100.00")


def test_nothing_assessed_gives_null_overall():
    s = compute_scores(ScoreInputs(total_records=5, invalid_records=0, completeness_columns=0, missing_cells=0))
    assert s["overall"] is None and s["valid_records"] == 5


def test_rounding_is_half_up_not_bankers():
    # 1 of 800 records failing is 0.125% failing: the exact tie 0.125 must round to 0.13 (half-up),
    # where banker's rounding would give 0.12.
    assert compute_scores(inputs(total_records=800, validity_failed_records=799))["validity"] == Decimal("0.13")
    assert compute_scores(inputs(total_records=800, validity_failed_records=1))["validity"] == Decimal("99.88")


def test_thirds_round_correctly():
    assert compute_scores(inputs(total_records=3, validity_failed_records=1))["validity"] == Decimal("66.67")
    assert compute_scores(inputs(total_records=3, validity_failed_records=2))["validity"] == Decimal("33.33")


def test_scores_are_decimals_with_two_places():
    s = compute_scores(inputs(total_records=7, validity_failed_records=2))
    assert s["validity"] == Decimal("71.43") and s["validity"].as_tuple().exponent == -2
