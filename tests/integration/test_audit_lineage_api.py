"""End-to-end tests for the audit trail and data lineage."""
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend import config

DATA = Path(__file__).resolve().parents[2] / "data"
TXN_ID, CUST_ID = 1, 2
KEY = json.loads((DATA / "sample_transactions_answer_key.json").read_text())
TOTAL = KEY["total_failures"]
HEADER = "transaction_id,customer_id,transaction_amount,transaction_date,merchant,transaction_status\n"
API = "/api/v1"


def _up(client, dataset_id, name, content=None):
    body = content if content is not None else (DATA / name).read_bytes()
    r = client.post(f"{API}/datasets/{dataset_id}/batches", files={"file": (name, body, "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()["batch_id"]


def _validate(client, bid):
    assert client.post(f"{API}/batches/{bid}/validate").status_code == 200


def _q(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


def _stored(bid) -> Path:
    return Path(config.get_settings().upload_dir) / f"{bid}.csv"


def _logs(client, **params):
    r = client.get(f"{API}/audit-logs", params={"limit": 500, **params})
    assert r.status_code == 200, r.text
    return r.json(), int(r.headers["X-Total-Count"])


def _exceptions(client, **params):
    return client.get(f"{API}/exceptions", params={"limit": 500, **params}).json()


@pytest.fixture()
def validated(client):
    _up(client, CUST_ID, "customers.csv")
    bid = _up(client, TXN_ID, "sample_transactions.csv")
    _validate(client, bid)
    return bid


# ===================================================================== audit trail

@pytest.fixture()
def full_workflow(client, restore_rules):
    """Every kind of audited action, in a known order. Returns the transactions batch id."""
    _up(client, CUST_ID, "customers.csv")                                                     # INGEST
    bid = _up(client, TXN_ID, "sample_transactions.csv")                                      # INGEST
    assert client.post(f"{API}/datasets/{TXN_ID}/batches",
                       files={"file": ("bad.csv", b"a,b\n1,2\n", "text/csv")}).status_code == 422   # INGEST FAILED
    _validate(client, bid)                                                                    # VALIDATE
    rid = _exceptions(client, batch_id=bid)[0]["result_id"]
    client.post(f"{API}/exceptions/{rid}/review", json={"status": "REVIEWED", "reviewer": "alice"})  # EXCEPTION_REVIEW
    client.get(f"{API}/reports/exceptions.csv", params={"batch_id": bid})                     # EXPORT_EXCEPTIONS
    rule = next(r for r in client.get(f"{API}/rules").json() if r["rule_code"] == "C01")
    client.patch(f"{API}/rules/{rule['rule_id']}", json={"severity": "CRITICAL", "updated_by": "admin"})  # RULE_UPDATE
    client.post(f"{API}/batches/{bid}/quality/recalculate", json={"requested_by": "lead"})    # SCORE
    return bid


def test_audit_trail_records_every_kind_of_action_newest_first(client, full_workflow):
    logs, total = _logs(client)
    assert total == 8 == len(logs)
    assert [(l["action"], l["execution_status"]) for l in logs] == [
        ("SCORE", "SUCCESS"), ("RULE_UPDATE", "SUCCESS"), ("EXPORT_EXCEPTIONS", "SUCCESS"),
        ("EXCEPTION_REVIEW", "SUCCESS"), ("VALIDATE", "SUCCESS"), ("INGEST", "FAILED"),
        ("INGEST", "SUCCESS"), ("INGEST", "SUCCESS")]
    ids = [l["log_id"] for l in logs]
    assert ids == sorted(ids, reverse=True)
    assert client.get(f"{API}/audit-logs/actions").json() == [
        "EXCEPTION_REVIEW", "EXPORT_EXCEPTIONS", "INGEST", "RULE_UPDATE", "SCORE", "VALIDATE"]


def test_validation_entry_has_everything_the_brief_requires(client, full_workflow):
    """Action, dataset, batch, execution time, rules executed, records processed, failures, status."""
    v = _logs(client, action="VALIDATE")[0][0]
    assert v["batch_id"] == full_workflow and v["dataset_name"] == "transactions" and v["source_file"] == "sample_transactions.csv"
    assert v["records_processed"] == 1000 and v["failure_count"] == TOTAL and v["duration_ms"] >= 0
    assert len(v["rules_executed"]) == 15 and {r["rule_code"] for r in v["rules_executed"]} >= {"C01", "R01", "U02"}
    assert sum(r["failures"] for r in v["rules_executed"]) == TOTAL
    assert v["actor"] == "system" and v["execution_status"] == "SUCCESS"


def test_failed_ingestion_is_audited_without_a_batch(client, full_workflow):
    f = _logs(client, status="FAILED")[0]
    assert len(f) == 1 and f[0]["batch_id"] is None and f[0]["source_file"] is None
    assert f[0]["details"]["kind"] == "missing_columns" and f[0]["details"]["source_file"] == "bad.csv"


def test_actor_and_action_are_recorded(client, full_workflow):
    assert [l["action"] for l in _logs(client, actor="alice")[0]] == ["EXCEPTION_REVIEW"]
    assert [l["action"] for l in _logs(client, actor="admin")[0]] == ["RULE_UPDATE"]
    assert _logs(client, actor="lead")[0][0]["action"] == "SCORE"
    assert _logs(client, actor="nobody")[1] == 0


def test_filters_combine(client, full_workflow):
    assert _logs(client, action="INGEST")[1] == 3
    assert _logs(client, action="INGEST", status="SUCCESS")[1] == 2
    assert _logs(client, action="INGEST", status="FAILED")[1] == 1
    by_batch, n = _logs(client, batch_id=full_workflow)
    assert n == 5 and all(l["batch_id"] == full_workflow for l in by_batch)       # INGEST, VALIDATE, REVIEW, EXPORT, SCORE
    assert _logs(client, batch_id=full_workflow, action="VALIDATE")[1] == 1


def test_time_range_filters(client, full_workflow):
    logs, _ = _logs(client)
    stamps = sorted(datetime.fromisoformat(l["action_timestamp"]) for l in logs)
    mid = stamps[3]
    expected = sum(1 for s in stamps if s >= mid)
    assert _logs(client, since=mid.isoformat())[1] == expected
    assert _logs(client, since=mid.replace(tzinfo=None).isoformat())[1] == expected          # naive = UTC
    assert _logs(client, until=mid.isoformat())[1] == sum(1 for s in stamps if s <= mid)
    assert _logs(client, since="2999-01-01T00:00:00")[1] == 0
    assert _logs(client, until="2000-01-01T00:00:00")[1] == 0
    assert _logs(client, since=stamps[0].isoformat(), until=stamps[-1].isoformat())[1] == 8
    r = client.get(f"{API}/audit-logs", params={"since": "2030-01-01T00:00:00", "until": "2020-01-01T00:00:00"})
    assert r.status_code == 422 and "since" in r.json()["detail"]


def test_audit_pagination_and_parameter_validation(client, full_workflow):
    page1 = client.get(f"{API}/audit-logs", params={"limit": 3})
    page2 = client.get(f"{API}/audit-logs", params={"limit": 3, "offset": 3})
    assert int(page1.headers["X-Total-Count"]) == 8
    assert not {l["log_id"] for l in page1.json()} & {l["log_id"] for l in page2.json()}
    for bad in ({"limit": 0}, {"limit": 501}, {"offset": -1}, {"status": "DONE"}, {"since": "yesterday"}, {"batch_id": "x"}):
        assert client.get(f"{API}/audit-logs", params=bad).status_code == 422
    assert client.get(f"{API}/audit-logs", params={"batch_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404


def test_get_single_entry_and_unknown_id(client, full_workflow):
    entry = _logs(client)[0][0]
    assert client.get(f"{API}/audit-logs/{entry['log_id']}").json() == entry
    assert client.get(f"{API}/audit-logs/999999").status_code == 404
    assert client.get(f"{API}/audit-logs/abc").status_code == 422


def test_no_audit_entry_contains_record_contents(client, full_workflow):
    blob = json.dumps(_logs(client)[0])
    source = (DATA / "sample_transactions.csv").read_text().splitlines()[1:50]
    assert not any(line in blob for line in source)
    merchants = {r["merchant"] for r in csv.DictReader((DATA / "sample_transactions.csv").open(encoding="utf-8")) if r["merchant"].strip()}
    assert not any(m in blob for m in merchants)


def test_the_api_offers_no_way_to_change_the_audit_trail(client, full_workflow):
    for method in ("put", "patch", "delete", "post"):
        assert getattr(client, method)(f"{API}/audit-logs/1").status_code == 405
    assert client.delete(f"{API}/audit-logs").status_code == 405


# ===================================================================== database-level immutability

def test_database_refuses_to_change_or_delete_audit_rows(client, engine, validated):
    for sql in ("UPDATE audit_logs SET actor='mallory'", "DELETE FROM audit_logs"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as c:
                c.execute(text(sql))
    assert _q(engine, "SELECT count(*) FROM audit_logs WHERE actor='mallory'")[0][0] == 0
    assert _q(engine, "SELECT count(*) FROM audit_logs")[0][0] >= 3


def test_database_refuses_to_change_or_delete_review_history(client, engine, validated):
    rid = _exceptions(client, batch_id=validated)[0]["result_id"]
    client.post(f"{API}/exceptions/{rid}/review", json={"status": "REVIEWED", "reviewer": "alice"})
    for sql in ("UPDATE exception_reviews SET reviewer='mallory'", "DELETE FROM exception_reviews"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as c:
                c.execute(text(sql))
    assert _q(engine, "SELECT reviewer FROM exception_reviews")[0][0] == "alice"


def test_a_batch_with_audit_history_cannot_be_deleted(client, engine, validated):
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM ingestion_batches WHERE batch_id=:b"), {"b": validated})
    assert _q(engine, "SELECT count(*) FROM ingestion_batches WHERE batch_id=:b", b=validated)[0][0] == 1
    assert _q(engine, "SELECT count(*) FROM transactions WHERE batch_id=:b", b=validated)[0][0] == 1000


def test_normal_inserts_still_work_under_the_triggers(client, validated):
    rid = _exceptions(client, batch_id=validated)[0]["result_id"]
    assert client.post(f"{API}/exceptions/{rid}/review", json={"status": "IN_REVIEW", "reviewer": "a"}).status_code == 200


# ===================================================================== batch lineage

def test_batch_lineage_traces_source_destination_and_results(client, validated):
    L = client.get(f"{API}/batches/{validated}/lineage").json()
    raw = (DATA / "sample_transactions.csv").read_bytes()
    assert L["batch_id"] == validated and L["dataset_name"] == "transactions" and L["processing_status"] == "COMPLETED"
    assert L["source"] == {"original_file_name": "sample_transactions.csv", "stored_file_name": f"{validated}.csv",
                           "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "stored_file_status": "verified"}
    assert L["destination"] == {"table": "transactions", "records_expected": 1000, "records_loaded": 1000,
                                "loaded_records_match": True}
    assert L["downstream"]["validation_failures"] == TOTAL
    assert L["downstream"]["exceptions_by_review_status"]["OPEN"] == TOTAL
    assert L["downstream"]["latest_quality"]["overall_score"] == KEY["expected_scores"]["overall"]
    assert L["ingestion_timestamp"] and L["completed_at"] and L["uploaded_by"] == "system"


def test_timeline_is_chronological_and_grows_with_activity(client, validated):
    assert [t["action"] for t in client.get(f"{API}/batches/{validated}/lineage").json()["timeline"]] == ["INGEST", "VALIDATE"]
    rid = _exceptions(client, batch_id=validated)[0]["result_id"]
    client.post(f"{API}/exceptions/{rid}/review", json={"status": "REVIEWED", "reviewer": "alice"})
    client.post(f"{API}/batches/{validated}/quality/recalculate")
    L = client.get(f"{API}/batches/{validated}/lineage").json()
    assert [t["action"] for t in L["timeline"]] == ["INGEST", "VALIDATE", "EXCEPTION_REVIEW", "SCORE"]
    ids = [t["log_id"] for t in L["timeline"]]
    assert ids == sorted(ids)
    assert L["timeline"][1]["failure_count"] == TOTAL and L["timeline"][1]["records_processed"] == 1000
    assert L["downstream"]["exceptions_by_review_status"]["REVIEWED"] == 1


def test_lineage_before_validation_has_no_downstream(client):
    _up(client, CUST_ID, "customers.csv")
    bid = _up(client, TXN_ID, "sample_transactions.csv")
    L = client.get(f"{API}/batches/{bid}/lineage").json()
    assert L["processing_status"] == "UPLOADED" and L["completed_at"] is None
    assert L["downstream"]["validation_failures"] == 0 and L["downstream"]["latest_quality"] is None
    assert [t["action"] for t in L["timeline"]] == ["INGEST"]


def test_reference_dataset_lineage(client):
    bid = _up(client, CUST_ID, "customers.csv")
    L = client.get(f"{API}/batches/{bid}/lineage").json()
    assert L["destination"] == {"table": "customers", "records_expected": 200, "records_loaded": None,
                                "loaded_records_match": None}
    assert L["source"]["stored_file_status"] == "verified" and L["downstream"]["latest_quality"] is None


def test_a_record_count_mismatch_between_file_and_database_is_flagged(client, engine):
    """If rows go missing after ingestion, lineage must say so instead of reporting a match."""
    _up(client, CUST_ID, "customers.csv")
    bid = _up(client, TXN_ID, "sample_transactions_clean.csv")
    with engine.begin() as c:
        c.execute(text("DELETE FROM transactions WHERE batch_id=:b AND source_row_number <= 3"), {"b": bid})
    d = client.get(f"{API}/batches/{bid}/lineage").json()["destination"]
    assert d["records_expected"] == 1000 and d["records_loaded"] == 997 and d["loaded_records_match"] is False


def test_tampered_original_file_is_detected(client, validated):
    p = _stored(validated)
    p.write_bytes(p.read_bytes().replace(b"T000150", b"T999999", 1))
    L = client.get(f"{API}/batches/{validated}/lineage").json()
    assert L["source"]["stored_file_status"] == "hash_mismatch"
    assert L["source"]["sha256"] == hashlib.sha256((DATA / "sample_transactions.csv").read_bytes()).hexdigest()   # recorded hash intact


def test_missing_original_file_is_reported_and_skipping_verification_works(client, validated):
    assert client.get(f"{API}/batches/{validated}/lineage?verify_file=false").json()["source"]["stored_file_status"] == "not_checked"
    _stored(validated).unlink()
    assert client.get(f"{API}/batches/{validated}/lineage").json()["source"]["stored_file_status"] == "missing"
    assert client.get(f"{API}/batches/{validated}/lineage?verify_file=false").json()["source"]["stored_file_status"] == "not_checked"


def test_unknown_batch_lineage(client):
    assert client.get(f"{API}/batches/00000000-0000-0000-0000-000000000000/lineage").status_code == 404
    assert client.get(f"{API}/batches/nope/lineage").status_code == 422


# ===================================================================== exception lineage

def _lineage(client, rid, **p):
    r = client.get(f"{API}/exceptions/{rid}/lineage", params=p)
    assert r.status_code == 200, r.text
    return r.json()


def test_exception_traces_back_to_its_row_in_the_original_file(client, validated):
    ex = next(e for e in _exceptions(client, batch_id=validated) if e["rule_code"] == "V03")
    L = _lineage(client, ex["result_id"])
    with (DATA / "sample_transactions.csv").open(newline="", encoding="utf-8") as f:
        source = list(csv.DictReader(f))[ex["row_number"] - 1]
    assert L["exception"]["rule_code"] == "V03" and L["exception"]["offending_value"] == source["transaction_date"]
    assert L["source_record"]["row_number"] == ex["row_number"]
    assert {k: (v or "") for k, v in L["source_record"]["values"].items()} == source
    assert L["matches_original_file"] is True
    assert L["batch"]["batch_id"] == validated and L["batch"]["source"]["original_file_name"] == "sample_transactions.csv"
    assert L["batch"]["source"]["stored_file_status"] == "verified"


def test_every_failure_in_the_sample_traces_to_a_matching_source_row(client, validated):
    """Row numbering, blank cells, whitespace-only cells and duplicates all line up with the file."""
    by_row = {}
    for e in _exceptions(client, batch_id=validated):
        by_row.setdefault(e["row_number"], e["result_id"])
    assert len(by_row) == KEY["invalid_records"]
    bad = [n for n, rid in by_row.items() if _lineage(client, rid)["matches_original_file"] is not True]
    assert bad == []


def test_a_changed_source_row_is_flagged_but_other_rows_still_match(client, validated):
    exs = _exceptions(client, batch_id=validated)
    target = next(e for e in exs if e["rule_code"] == "V03")
    other = next(e for e in exs if e["rule_code"] == "V04")
    p = _stored(validated)
    lines = p.read_text(encoding="utf-8").split("\n")
    lines[target["row_number"]] = lines[target["row_number"]].replace(target["offending_value"], "2099-01-01")
    p.write_text("\n".join(lines), encoding="utf-8")
    t = _lineage(client, target["result_id"])
    assert t["matches_original_file"] is False and t["batch"]["source"]["stored_file_status"] == "hash_mismatch"
    assert _lineage(client, other["result_id"])["matches_original_file"] is True      # its own row is untouched


def test_missing_file_gives_unknown_not_false(client, validated):
    rid = _exceptions(client, batch_id=validated)[0]["result_id"]
    _stored(validated).unlink()
    L = _lineage(client, rid)
    assert L["matches_original_file"] is None and L["batch"]["source"]["stored_file_status"] == "missing"
    assert _lineage(client, rid, verify_file="false")["matches_original_file"] is None


def test_row_numbers_survive_blank_lines_quotes_and_reordered_columns(client):
    _up(client, CUST_ID, "customers.csv")
    content = ("Merchant,Transaction ID,Customer-ID,Transaction Amount,Transaction Date,Transaction Status,Note\n"
               '"Shop, Inc",T1,C0001,10.00,2026-01-01,COMPLETED,x\n'
               "\n"
               '"Multi\nLine",T2,C0001,abc,2026-01-01,COMPLETED,y\n'
               "\n\n"
               "Cafe,T3,C0002,5.00,2026-01-01,COMPLETED,z\n").encode()
    bid = _up(client, TXN_ID, "weird.csv", content)
    _validate(client, bid)
    ex = _exceptions(client, batch_id=bid)
    assert [(e["rule_code"], e["row_number"]) for e in ex] == [("V01", 2)]            # blank lines do not shift numbering
    L = _lineage(client, ex[0]["result_id"])
    assert L["source_record"]["values"]["merchant"] == "Multi\nLine"
    assert L["matches_original_file"] is True


def test_exception_lineage_for_unknown_id(client, validated):
    assert client.get(f"{API}/exceptions/999999/lineage").status_code == 404
    assert client.get(f"{API}/exceptions/abc/lineage").status_code == 422
