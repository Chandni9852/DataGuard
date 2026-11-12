"""End-to-end validation tests: upload through the API, validate, and compare the database
contents with the generator's answer key."""
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.services.rules import REGISTRY

DATA = Path(__file__).resolve().parents[2] / "data"
TXN_ID, CUST_ID = 1, 2
KEY = json.loads((DATA / "sample_transactions_answer_key.json").read_text())


def _up(client, dataset_id, name, content=None, **form):
    body = content if content is not None else (DATA / name).read_bytes()
    r = client.post(f"/api/v1/datasets/{dataset_id}/batches", files={"file": (name, body, "text/csv")}, data=form)
    assert r.status_code == 201, r.text
    return r.json()["batch_id"]


def _validate(client, bid, **body):
    return client.post(f"/api/v1/batches/{bid}/validate", json=body or None)


def _q(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


def _scalar(engine, sql, **p):
    rows = _q(engine, sql, **p)
    return rows[0][0] if rows else None


@pytest.fixture()
def loaded(client):
    """Customers loaded and the corrupted transactions file ingested; returns the batch id."""
    _up(client, CUST_ID, "customers.csv")
    return _up(client, TXN_ID, "sample_transactions.csv")


# --------------------------------------------------------------------- correctness

def test_results_in_database_match_the_answer_key_row_by_row(client, engine, loaded):
    r = _validate(client, loaded)
    assert r.status_code == 200, r.text
    stored = {(n, code) for n, code in _q(engine, """
        SELECT v.row_number, r.rule_code FROM validation_results v
        JOIN validation_rules r USING (rule_id) WHERE v.batch_id = :b""", b=loaded)}
    expected = {(r_["row_number"], e["rule_code"]) for r_ in KEY["rows"] for e in r_["expected"]}
    assert stored == expected


def test_summary_matches_the_answer_key(client, loaded):
    s = _validate(client, loaded).json()
    assert s["processing_status"] == "COMPLETED"
    assert s["total_records"] == 1000
    assert s["invalid_records"] == KEY["invalid_records"] and s["valid_records"] == KEY["valid_records"]
    assert s["total_failures"] == KEY["total_failures"]
    assert {x["rule_code"]: x["failures"] for x in s["failures_by_rule"]} == KEY["expected_failures_by_rule"]
    assert s["failures_by_category"] == KEY["expected_failures_by_category"]
    assert sum(s["failures_by_severity"].values()) == s["total_failures"]
    assert s["duration_ms"] is not None and len(s["rules_executed"]) == 15


def test_stored_failures_carry_everything_the_brief_requires(client, engine, loaded):
    """Record identifier, column, rule, description, severity, detection time, batch id."""
    _validate(client, loaded)
    row = _q(engine, """
        SELECT v.batch_id, v.record_identifier, v.column_name, v.offending_value, v.error_description,
               v.detected_at, r.rule_code, r.severity, t.source_row_number
        FROM validation_results v JOIN validation_rules r USING (rule_id)
        JOIN transactions t ON t.record_id = v.record_id
        WHERE v.batch_id=:b AND r.rule_code='V02' AND v.offending_value LIKE '-%' LIMIT 1""", b=loaded)[0]
    assert str(row.batch_id) == loaded
    assert row.column_name == "transaction_amount" and row.severity == "HIGH"
    assert row.error_description and row.detected_at is not None
    assert row.record_identifier and row.record_identifier.startswith("T")


def test_missing_ids_are_reported_with_null_identifier_but_linked_by_record(client, engine, loaded):
    _validate(client, loaded)
    rows = _q(engine, """
        SELECT v.record_identifier, v.record_id, t.source_row_number, v.row_number FROM validation_results v
        JOIN validation_rules r USING (rule_id) JOIN transactions t ON t.record_id = v.record_id
        WHERE v.batch_id=:b AND r.rule_code='C01'""", b=loaded)
    assert len(rows) == 3
    assert all(r.record_identifier is None and r.row_number == r.source_row_number for r in rows)


def test_source_records_are_never_modified_or_deleted(client, engine, loaded):
    before = _q(engine, "SELECT * FROM transactions WHERE batch_id=:b ORDER BY record_id", b=loaded)
    _validate(client, loaded)
    after = _q(engine, "SELECT * FROM transactions WHERE batch_id=:b ORDER BY record_id", b=loaded)
    assert before == after and len(after) == 1000


def test_audit_entry_for_the_run(client, engine, loaded):
    _validate(client, loaded, requested_by="qa.analyst")
    a = _q(engine, "SELECT * FROM audit_logs WHERE batch_id=:b AND action='VALIDATE'", b=loaded)
    assert len(a) == 1
    a = a[0]
    assert a.execution_status == "SUCCESS" and a.actor == "qa.analyst"
    assert a.records_processed == 1000 and a.failure_count == KEY["total_failures"] and a.duration_ms >= 0
    assert {r["rule_code"]: r["failures"] for r in a.rules_executed} == {
        **{c: 0 for c in REGISTRY}, **KEY["expected_failures_by_rule"]}


def test_clean_file_validates_with_zero_failures(client, engine):
    _up(client, CUST_ID, "customers.csv")
    bid = _up(client, TXN_ID, "sample_transactions_clean.csv")
    s = _validate(client, bid).json()
    assert s["total_failures"] == 0 and s["invalid_records"] == 0 and s["valid_records"] == 1000
    assert _scalar(engine, "SELECT count(*) FROM validation_results") == 0


def test_validation_summary_endpoint_before_and_after(client, loaded):
    before = client.get(f"/api/v1/batches/{loaded}/validation-summary").json()
    assert before["processing_status"] == "UPLOADED" and before["valid_records"] is None
    assert before["total_failures"] == 0 and before["duration_ms"] is None
    _validate(client, loaded)
    after = client.get(f"/api/v1/batches/{loaded}/validation-summary").json()
    assert after["total_failures"] == KEY["total_failures"] and after["valid_records"] == KEY["valid_records"]


def test_batch_status_and_completion_time_after_validation(client, loaded):
    _validate(client, loaded)
    b = client.get(f"/api/v1/batches/{loaded}").json()
    assert b["processing_status"] == "COMPLETED" and b["completed_at"] is not None


def test_two_batches_are_validated_independently(client, engine):
    _up(client, CUST_ID, "customers.csv")
    dirty = _up(client, TXN_ID, "sample_transactions.csv")
    clean = _up(client, TXN_ID, "sample_transactions_clean.csv")
    _validate(client, dirty)
    _validate(client, clean)
    assert _scalar(engine, "SELECT count(*) FROM validation_results WHERE batch_id=:b", b=clean) == 0
    assert _scalar(engine, "SELECT count(*) FROM validation_results WHERE batch_id=:b", b=dirty) == KEY["total_failures"]


def test_empty_batch_validates_to_zero_failures(client):
    _up(client, CUST_ID, "customers.csv")
    header = "transaction_id,customer_id,transaction_amount,transaction_date,merchant,transaction_status\n"
    bid = _up(client, TXN_ID, "empty.csv", header.encode())
    s = _validate(client, bid).json()
    assert s["total_records"] == 0 and s["total_failures"] == 0 and s["processing_status"] == "COMPLETED"


# --------------------------------------------------------------------- state machine and refusal paths

def test_completed_batch_cannot_be_validated_again(client, engine, loaded):
    _validate(client, loaded)
    r = _validate(client, loaded)
    assert r.status_code == 409 and r.json()["status"] == "COMPLETED"
    assert _scalar(engine, "SELECT count(*) FROM validation_results WHERE batch_id=:b", b=loaded) == KEY["total_failures"]


def test_batch_already_being_validated_is_refused(client, engine, loaded):
    with engine.begin() as c:
        c.execute(text("UPDATE ingestion_batches SET processing_status='VALIDATING' WHERE batch_id=:b"), {"b": loaded})
    r = _validate(client, loaded)
    assert r.status_code == 409 and r.json()["status"] == "VALIDATING"


def test_unknown_batch_404_and_bad_uuid_422(client):
    assert _validate(client, "00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.post("/api/v1/batches/nope/validate").status_code == 422


def test_reference_dataset_batches_are_not_validatable(client):
    bid = _up(client, CUST_ID, "customers.csv")
    r = _validate(client, bid)
    assert r.status_code == 400 and "reference data" in r.json()["detail"]


def test_validating_without_customers_is_refused_and_leaves_batch_untouched(client, engine):
    bid = _up(client, TXN_ID, "sample_transactions.csv")
    r = _validate(client, bid)
    assert r.status_code == 409 and r.json()["code"] == "reference_missing"
    assert client.get(f"/api/v1/batches/{bid}").json()["processing_status"] == "UPLOADED"
    assert _scalar(engine, "SELECT count(*) FROM validation_results") == 0
    failed = _q(engine, "SELECT details FROM audit_logs WHERE action='VALIDATE' AND execution_status='FAILED'")
    assert len(failed) == 1 and failed[0].details["kind"] == "reference_missing"
    # after loading the reference data the same batch validates fine
    _up(client, CUST_ID, "customers.csv")
    assert _validate(client, bid).status_code == 200


def test_bad_requested_by_is_rejected(client, loaded):
    assert _validate(client, loaded, requested_by="x'; DROP TABLE y;--").status_code == 400


# --------------------------------------------------------------------- atomicity

def test_failure_mid_run_leaves_no_partial_results_marks_failed_and_allows_retry(client, engine, loaded, monkeypatch):
    from backend.services import validation
    original = validation.execute_rules

    def boom(df, rules, ctx):
        original(df, rules, ctx)            # do the real work first
        raise RuntimeError("simulated crash after evaluation")
    monkeypatch.setattr(validation, "execute_rules", boom)
    with pytest.raises(RuntimeError):
        client.post(f"/api/v1/batches/{loaded}/validate")
    # TestClient re-raises unhandled errors by default; the state afterwards is what matters
    assert _scalar(engine, "SELECT count(*) FROM validation_results") == 0
    assert client.get(f"/api/v1/batches/{loaded}").json()["processing_status"] == "FAILED"
    failed = _q(engine, "SELECT details FROM audit_logs WHERE action='VALIDATE' AND execution_status='FAILED'")
    assert len(failed) == 1 and failed[0].details["kind"] == "internal_error"
    assert "simulated" not in json.dumps(failed[0].details)        # internal detail is not stored

    monkeypatch.setattr(validation, "execute_rules", original)
    again = _validate(client, loaded)
    assert again.status_code == 200 and again.json()["total_failures"] == KEY["total_failures"]
    assert client.get(f"/api/v1/batches/{loaded}").json()["processing_status"] == "COMPLETED"


# --------------------------------------------------------------------- rule configuration

def test_rules_endpoint_lists_all_fifteen(client):
    r = client.get("/api/v1/rules")
    assert r.status_code == 200 and len(r.json()) == 15
    assert all(x["implemented"] and x["is_active"] for x in r.json())
    assert {x["rule_category"] for x in r.json()} == {
        "COMPLETENESS", "UNIQUENESS", "VALIDITY", "CONSISTENCY", "REFERENTIAL_INTEGRITY"}


def _rule_id(client, code):
    return next(r["rule_id"] for r in client.get("/api/v1/rules").json() if r["rule_code"] == code)


def test_deactivating_a_rule_removes_its_failures_and_is_audited(client, engine, loaded, restore_rules):
    rid = _rule_id(client, "C06")
    r = client.patch(f"/api/v1/rules/{rid}", json={"is_active": False, "updated_by": "admin"})
    assert r.status_code == 200 and r.json()["is_active"] is False
    s = _validate(client, loaded).json()
    codes = {x["rule_code"] for x in s["failures_by_rule"]}
    assert "C06" not in codes and len(s["rules_executed"]) == 14
    assert s["total_failures"] == KEY["total_failures"] - KEY["expected_failures_by_rule"]["C06"]
    a = _q(engine, "SELECT actor, details FROM audit_logs WHERE action='RULE_UPDATE'")
    assert len(a) == 1 and a[0].actor == "admin"
    assert a[0].details["before"]["is_active"] is True and a[0].details["after"]["is_active"] is False


def test_severity_change_is_reflected_in_the_summary(client, loaded, restore_rules):
    client.patch(f"/api/v1/rules/{_rule_id(client, 'V04')}", json={"severity": "CRITICAL"})
    s = _validate(client, loaded).json()
    assert next(x for x in s["failures_by_rule"] if x["rule_code"] == "V04")["severity"] == "CRITICAL"


def test_parameter_change_alters_results(client, loaded, restore_rules):
    """Allowing lowercase 'completed' removes exactly the failures that value caused."""
    rid = _rule_id(client, "V04")
    r = client.patch(f"/api/v1/rules/{rid}", json={"parameters": {
        "column": "transaction_status", "allowed": ["COMPLETED", "PENDING", "FAILED", "REVERSED", "completed"]}})
    assert r.status_code == 200
    s = _validate(client, loaded).json()
    v04 = next(x for x in s["failures_by_rule"] if x["rule_code"] == "V04")["failures"]
    assert v04 == KEY["expected_failures_by_rule"]["V04"] - 1


@pytest.mark.parametrize("body", [
    {"parameters": {"allowed": []}},
    {"parameters": {"allowed": "COMPLETED"}},
    {"severity": "URGENT"},
])
def test_invalid_rule_updates_are_rejected_and_change_nothing(client, engine, restore_rules, body):
    before = client.get("/api/v1/rules").json()
    r = client.patch(f"/api/v1/rules/{_rule_id(client, 'V04')}", json=body)
    assert r.status_code == 422
    assert client.get("/api/v1/rules").json() == before
    assert _scalar(engine, "SELECT count(*) FROM audit_logs WHERE action='RULE_UPDATE'") == 0


def test_update_unknown_rule_404(client):
    assert client.patch("/api/v1/rules/9999", json={"is_active": False}).status_code == 404


def test_misconfigured_rule_in_database_fails_validation_cleanly(client, engine, loaded, restore_rules):
    with engine.begin() as c:     # bypass the API to simulate bad data in the table
        c.execute(text("""UPDATE validation_rules SET parameters='{"allowed": []}' WHERE rule_code='V04'"""))
    r = _validate(client, loaded)
    assert r.status_code == 500 and r.json()["code"] == "rule_config" and r.json()["rule_code"] == "V04"
    assert client.get(f"/api/v1/batches/{loaded}").json()["processing_status"] == "UPLOADED"


def test_active_rule_without_implementation_fails_cleanly(client, engine, loaded, restore_rules):
    with engine.begin() as c:
        c.execute(text("""INSERT INTO validation_rules (rule_code, rule_name, rule_category, rule_description, severity)
                          VALUES ('X99','Ghost','VALIDITY','no code behind it','LOW')"""))
    r = _validate(client, loaded)
    assert r.status_code == 500 and "no implementation" in r.json()["detail"]
    listed = {x["rule_code"]: x["implemented"] for x in client.get("/api/v1/rules").json()}
    assert listed["X99"] is False
