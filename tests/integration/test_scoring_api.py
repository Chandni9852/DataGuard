"""End-to-end scoring tests against a real database, checked against the generator's answer key."""
import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

DATA = Path(__file__).resolve().parents[2] / "data"
TXN_ID, CUST_ID = 1, 2
KEY = json.loads((DATA / "sample_transactions_answer_key.json").read_text())
EXPECTED = KEY["expected_scores"]
HEADER = "transaction_id,customer_id,transaction_amount,transaction_date,merchant,transaction_status\n"


def _up(client, dataset_id, name, content=None):
    body = content if content is not None else (DATA / name).read_bytes()
    r = client.post(f"/api/v1/datasets/{dataset_id}/batches", files={"file": (name, body, "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()["batch_id"]


def _validate(client, bid):
    r = client.post(f"/api/v1/batches/{bid}/validate")
    assert r.status_code == 200, r.text
    return r.json()


def _q(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


def _scores(q):
    return {k: q[f"{k}_score"] for k in ("completeness", "uniqueness", "validity", "consistency", "overall")}


def _rule_id(client, code):
    return next(r["rule_id"] for r in client.get("/api/v1/rules").json() if r["rule_code"] == code)


@pytest.fixture()
def loaded(client):
    _up(client, CUST_ID, "customers.csv")
    return _up(client, TXN_ID, "sample_transactions.csv")


# --------------------------------------------------------------------- the headline numbers

def test_validation_stores_scores_that_match_the_answer_key(client, loaded):
    summary = _validate(client, loaded)
    q = summary["quality"]
    assert _scores(q) == EXPECTED
    assert q["status"] == "scored" and len(q["dimensions_assessed"]) == 4
    assert (q["total_records"], q["valid_records"], q["invalid_records"]) == (1000, KEY["valid_records"], KEY["invalid_records"])
    assert client.get(f"/api/v1/batches/{loaded}/quality").json() == q


def test_database_row_holds_the_same_numbers(client, engine, loaded):
    _validate(client, loaded)
    rows = _q(engine, "SELECT * FROM quality_metrics WHERE batch_id=:b", b=loaded)
    assert len(rows) == 1
    m = rows[0]
    assert [float(m.completeness_score), float(m.uniqueness_score), float(m.validity_score),
            float(m.consistency_score), float(m.overall_score)] == [
        EXPECTED["completeness"], EXPECTED["uniqueness"], EXPECTED["validity"], EXPECTED["consistency"], EXPECTED["overall"]]


def test_overall_is_the_mean_of_the_four_displayed_scores(client, loaded):
    q = _validate(client, loaded)["quality"]
    mean = (q["completeness_score"] + q["uniqueness_score"] + q["validity_score"] + q["consistency_score"]) / 4
    assert abs(q["overall_score"] - mean) <= 0.005


def test_scores_agree_with_the_validation_summary(client, loaded):
    s = _validate(client, loaded)
    assert s["quality"]["valid_records"] == s["valid_records"]
    assert s["quality"]["invalid_records"] == s["invalid_records"]
    assert s["quality"]["valid_records"] + s["quality"]["invalid_records"] == s["total_records"]
    again = client.get(f"/api/v1/batches/{loaded}/validation-summary").json()
    assert again["quality"] == s["quality"]


def test_clean_batch_scores_100(client):
    _up(client, CUST_ID, "customers.csv")
    q = _validate(client, _up(client, TXN_ID, "sample_transactions_clean.csv"))["quality"]
    assert _scores(q) == {k: 100.0 for k in ("completeness", "uniqueness", "validity", "consistency", "overall")}
    assert q["invalid_records"] == 0


def test_a_record_with_many_failures_lowers_each_dimension_once(client):
    """One bad record out of ten: V02 + V04 (validity) and R01 (consistency) on the same row."""
    _up(client, CUST_ID, "customers.csv")
    rows = ["T0,ZZZ,-5.00,2026-01-01,Shop,BAD"] + [f"T{i},C0001,10.00,2026-01-01,Shop,COMPLETED" for i in range(1, 10)]
    q = _validate(client, _up(client, TXN_ID, "tiny.csv", (HEADER + "\n".join(rows) + "\n").encode()))["quality"]
    assert (q["completeness_score"], q["uniqueness_score"]) == (100.0, 100.0)
    assert q["validity_score"] == 90.0 and q["consistency_score"] == 90.0     # 1 record each, not 2 failures
    assert q["overall_score"] == 95.0
    assert (q["valid_records"], q["invalid_records"]) == (9, 1)


def test_missing_cells_are_counted_per_cell(client):
    """Two blank cells in one record cost two cells of completeness but one invalid record."""
    _up(client, CUST_ID, "customers.csv")
    rows = [",C0001,10.00,2026-01-01,,COMPLETED"] + [f"T{i},C0001,10.00,2026-01-01,Shop,COMPLETED" for i in range(1, 10)]
    q = _validate(client, _up(client, TXN_ID, "blank.csv", (HEADER + "\n".join(rows) + "\n").encode()))["quality"]
    assert q["completeness_score"] == pytest.approx(96.67)       # 58 of 60 cells
    assert q["invalid_records"] == 1 and q["uniqueness_score"] == 100.0


# --------------------------------------------------------------------- empty and not-yet-scored

def test_empty_batch_is_no_data_not_100(client, engine):
    _up(client, CUST_ID, "customers.csv")
    q = _validate(client, _up(client, TXN_ID, "empty.csv", HEADER.encode()))["quality"]
    assert q["status"] == "no_data" and q["overall_score"] is None and q["dimensions_assessed"] == []
    assert q["total_records"] == 0 and q["valid_records"] == 0
    row = _q(engine, "SELECT overall_score, completeness_score FROM quality_metrics")[0]
    assert row.overall_score is None and row.completeness_score is None


def test_quality_before_validation_is_404_and_summary_has_none(client, loaded):
    r = client.get(f"/api/v1/batches/{loaded}/quality")
    assert r.status_code == 404 and "not been scored" in r.json()["detail"]
    assert client.get(f"/api/v1/batches/{loaded}/validation-summary").json()["quality"] is None


def test_unknown_batch_and_bad_id(client):
    zero = "00000000-0000-0000-0000-000000000000"
    for suffix in ("quality", "quality/history"):
        assert client.get(f"/api/v1/batches/{zero}/{suffix}").status_code == 404
    assert client.post(f"/api/v1/batches/{zero}/quality/recalculate").status_code == 404
    assert client.get("/api/v1/batches/nope/quality").status_code == 422


# --------------------------------------------------------------------- rule configuration effects

def test_switched_off_rule_changes_which_cells_are_expected(client, loaded, restore_rules):
    """Merchant is no longer mandatory: 5 columns are expected and its 10 blanks are not counted."""
    client.patch(f"/api/v1/rules/{_rule_id(client, 'C06')}", json={"is_active": False})
    q = _validate(client, loaded)["quality"]
    assert q["completeness_score"] == 99.56                       # (5000 - 22) / 5000
    assert q["uniqueness_score"] == EXPECTED["uniqueness"]
    assert q["overall_score"] == 98.17                            # (99.56 + 98.7 + 96.4 + 98.0) / 4, half-up


def test_dimension_with_every_rule_off_is_null_and_not_100(client, loaded, restore_rules):
    for code in ("U01", "U02"):
        client.patch(f"/api/v1/rules/{_rule_id(client, code)}", json={"is_active": False})
    q = _validate(client, loaded)["quality"]
    assert q["uniqueness_score"] is None and "uniqueness" not in q["dimensions_assessed"]
    assert q["overall_score"] == 97.96                            # mean of the three assessed: 99.47, 96.4, 98.0
    assert q["valid_records"] + q["invalid_records"] == 1000


def test_recalculation_uses_the_rules_that_ran_not_todays_configuration(client, loaded, restore_rules):
    before = _validate(client, loaded)["quality"]
    for code in ("C06", "U01", "U02"):                            # switch rules off after the run
        client.patch(f"/api/v1/rules/{_rule_id(client, code)}", json={"is_active": False})
    after = client.post(f"/api/v1/batches/{loaded}/quality/recalculate").json()
    assert _scores(after) == _scores(before)
    assert after["metric_id"] != before["metric_id"]


# --------------------------------------------------------------------- history and recalculation

def test_recalculate_appends_history_and_is_audited(client, engine, loaded):
    first = _validate(client, loaded)["quality"]
    r = client.post(f"/api/v1/batches/{loaded}/quality/recalculate", json={"requested_by": "lead.analyst"})
    assert r.status_code == 200
    second = r.json()
    assert _scores(second) == _scores(first) and second["metric_id"] > first["metric_id"]

    hist = client.get(f"/api/v1/batches/{loaded}/quality/history").json()
    assert [h["metric_id"] for h in hist] == [second["metric_id"], first["metric_id"]]     # newest first
    assert client.get(f"/api/v1/batches/{loaded}/quality").json()["metric_id"] == second["metric_id"]
    audit = _q(engine, "SELECT actor, details FROM audit_logs WHERE action='SCORE' AND batch_id=:b", b=loaded)
    assert len(audit) == 1 and audit[0].actor == "lead.analyst" and audit[0].details["metric_id"] == second["metric_id"]


def test_recalculate_requires_a_completed_batch(client, loaded):
    r = client.post(f"/api/v1/batches/{loaded}/quality/recalculate")
    assert r.status_code == 409 and "UPLOADED" in r.json()["detail"]


def test_recalculate_rejects_a_bad_actor(client, loaded):
    _validate(client, loaded)
    assert client.post(f"/api/v1/batches/{loaded}/quality/recalculate",
                       json={"requested_by": "x'; DROP TABLE y;--"}).status_code == 400


def test_trend_lists_the_latest_score_per_batch_oldest_first(client):
    _up(client, CUST_ID, "customers.csv")
    dirty = _up(client, TXN_ID, "sample_transactions.csv")
    clean = _up(client, TXN_ID, "sample_transactions_clean.csv")
    unvalidated = _up(client, TXN_ID, "tiny.csv", (HEADER + "T1,C0001,5,2026-01-01,Shop,PENDING\n").encode())
    _validate(client, dirty)
    _validate(client, clean)
    client.post(f"/api/v1/batches/{dirty}/quality/recalculate")      # a second row must not duplicate the point

    t = client.get("/api/v1/metrics/trend").json()
    assert [p["batch_id"] for p in t] == [dirty, clean]
    assert [p["overall_score"] for p in t] == [EXPECTED["overall"], 100.0]
    assert t[0]["source_file"] == "sample_transactions.csv" and t[0]["ingestion_timestamp"] < t[1]["ingestion_timestamp"]
    assert unvalidated not in [p["batch_id"] for p in t]
    assert [p["batch_id"] for p in client.get("/api/v1/metrics/trend?limit=1").json()] == [clean]
    assert client.get(f"/api/v1/metrics/trend?dataset_id={CUST_ID}").json() == []
    assert client.get("/api/v1/metrics/trend?limit=0").status_code == 422


# --------------------------------------------------------------------- integrity

def test_scoring_failure_rolls_back_the_whole_validation(client, engine, loaded, monkeypatch):
    from backend.services import quality_scoring

    def boom(*a, **k):
        raise RuntimeError("scoring blew up")
    monkeypatch.setattr(quality_scoring, "calculate_and_store", boom)
    with pytest.raises(RuntimeError):
        client.post(f"/api/v1/batches/{loaded}/validate")
    assert _q(engine, "SELECT count(*) FROM validation_results")[0][0] == 0      # no results without scores
    assert _q(engine, "SELECT count(*) FROM quality_metrics")[0][0] == 0
    assert client.get(f"/api/v1/batches/{loaded}").json()["processing_status"] == "FAILED"
    monkeypatch.undo()
    assert _scores(_validate(client, loaded)["quality"]) == EXPECTED            # retry succeeds


def test_database_rejects_out_of_range_scores(client, engine, loaded):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO quality_metrics (batch_id, overall_score) VALUES (:b, 100.01)"), {"b": loaded})
