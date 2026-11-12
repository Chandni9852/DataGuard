"""End-to-end ingestion tests through the HTTP API against a real PostgreSQL database."""
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from backend import config

DATA = Path(__file__).resolve().parents[2] / "data"
TXN_ID, CUST_ID = 1, 2          # seeded dataset ids (transactions, customers)
HEADER = "transaction_id,customer_id,transaction_amount,transaction_date,merchant,transaction_status"


def _files(name: str, content: bytes | None = None, ctype: str = "text/csv"):
    return {"file": (name, content if content is not None else (DATA / name).read_bytes(), ctype)}


def _scalar(engine, sql, **params):
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar()


def _upload(client, dataset_id, name, content=None, **form):
    return client.post(f"/api/v1/datasets/{dataset_id}/batches", files=_files(name, content), data=form)


# ------------------------------------------------------------------ datasets / preview

def test_lists_seeded_datasets(client):
    r = client.get("/api/v1/datasets")
    assert r.status_code == 200
    assert [d["dataset_name"] for d in r.json()] == ["transactions", "customers"]


def test_preview_returns_profile_and_persists_nothing(client, engine):
    r = client.post(f"/api/v1/datasets/{TXN_ID}/preview?rows=5", files=_files("sample_transactions.csv"))
    assert r.status_code == 200
    body = r.json()
    assert body["total_rows"] == 1000 and body["schema_valid"] is True
    assert len(body["preview_rows"]) == 5
    assert body["sha256"] == hashlib.sha256((DATA / "sample_transactions.csv").read_bytes()).hexdigest()
    assert {c["name"] for c in body["columns"]} == set(body["required_columns"])
    assert _scalar(engine, "SELECT count(*) FROM ingestion_batches") == 0
    assert _scalar(engine, "SELECT count(*) FROM transactions") == 0
    assert _scalar(engine, "SELECT count(*) FROM audit_logs") == 0


def test_preview_reports_missing_columns_with_200_and_schema_invalid(client):
    r = client.post(f"/api/v1/datasets/{TXN_ID}/preview", files=_files("t.csv", b"transaction_id,merchant\n1,x\n"))
    assert r.status_code == 200
    body = r.json()
    assert body["schema_valid"] is False
    assert "customer_id" in body["missing_required_columns"]


def test_preview_flags_unexpected_and_renamed_columns(client):
    csv_bytes = (HEADER.replace("merchant", "Merchant") + ",note\nT1,C1,5,2025-01-01,x,PENDING,hi\n").encode()
    body = client.post(f"/api/v1/datasets/{TXN_ID}/preview", files=_files("t.csv", csv_bytes)).json()
    assert body["schema_valid"] is True
    assert body["unexpected_columns"] == ["note"]
    assert body["renamed_columns"] == {"Merchant": "merchant"}
    assert len(body["warnings"]) == 2


# ------------------------------------------------------------------ happy path

def test_ingest_transactions_loads_every_row_raw_and_records_lineage(client, engine):
    r = _upload(client, TXN_ID, "sample_transactions.csv", uploaded_by="analyst@example.com")
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["total_records"] == 1000
    assert b["processing_status"] == "UPLOADED"
    assert b["destination_table"] == "transactions"
    assert b["source_file"] == "sample_transactions.csv"
    assert b["uploaded_by"] == "analyst@example.com"
    assert b["file_sha256"] == hashlib.sha256((DATA / "sample_transactions.csv").read_bytes()).hexdigest()
    assert b["completed_at"] is None

    bid = b["batch_id"]
    assert _scalar(engine, "SELECT count(*) FROM transactions WHERE batch_id=:b", b=bid) == 1000

    # original bytes preserved under the generated name (lineage)
    stored = Path(config.get_settings().upload_dir) / f"{bid}.csv"
    assert stored.read_bytes() == (DATA / "sample_transactions.csv").read_bytes()

    # audit entry
    row = _scalar(engine, "SELECT row_to_json(a) FROM audit_logs a WHERE batch_id=:b", b=bid)
    assert row["action"] == "INGEST" and row["execution_status"] == "SUCCESS"
    assert row["records_processed"] == 1000 and row["actor"] == "analyst@example.com"
    assert row["details"]["sha256"] == b["file_sha256"]


def test_raw_values_and_row_numbers_are_preserved_exactly(client, engine):
    """Bad data must be stored as-is and addressable by the generator's answer key row numbers."""
    key = json.loads((DATA / "sample_transactions_answer_key.json").read_text())
    bid = _upload(client, TXN_ID, "sample_transactions.csv").json()["batch_id"]

    # Only entirely empty cells become NULL; whitespace-only cells are kept as evidence.
    counts = lambda col: (_scalar(engine, f"SELECT count(*) FROM transactions WHERE batch_id=:b AND {col} IS NULL", b=bid))
    assert counts("customer_id") == key["expected_failures_by_rule"]["C02"]
    assert counts("transaction_id") == key["expected_failures_by_rule"]["C01"]
    assert _scalar(engine, "SELECT count(*) FROM transactions WHERE batch_id=:b AND merchant IS NULL", b=bid) == 8
    assert _scalar(engine, "SELECT count(*) FROM transactions WHERE batch_id=:b AND merchant='   '", b=bid) == 2
    assert _scalar(engine, "SELECT count(*) FROM transactions WHERE batch_id=:b AND merchant IS NOT NULL "
                           "AND btrim(merchant)=''", b=bid) == 2

    # every defective row sits at the row number the answer key says
    for r in key["rows"]:
        stored = _scalar(engine, "SELECT transaction_id FROM transactions WHERE batch_id=:b AND source_row_number=:n",
                         b=bid, n=r["row_number"])
        assert (stored or "") == r["transaction_id"]

    # invalid values are stored verbatim, not rejected or coerced
    assert _scalar(engine, "SELECT count(*) FROM transactions WHERE batch_id=:b AND transaction_amount='abc'", b=bid) == 1
    assert _scalar(engine, "SELECT count(*) FROM transactions WHERE batch_id=:b AND transaction_date='31/12/2025'", b=bid) == 1
    assert _scalar(engine, "SELECT count(*) FROM transactions WHERE batch_id=:b AND transaction_amount LIKE '-%'", b=bid) >= 12


def test_clean_file_ingests_without_warnings(client):
    r = _upload(client, TXN_ID, "sample_transactions_clean.csv")
    assert r.status_code == 201 and r.json()["warnings"] == []


def test_customers_are_loaded_as_reference_data_and_completed(client, engine):
    r = _upload(client, CUST_ID, "customers.csv")
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["total_records"] == 200
    assert b["processing_status"] == "COMPLETED" and b["completed_at"] is not None
    assert b["destination_table"] == "customers"
    assert _scalar(engine, "SELECT count(*) FROM customers") == 200


def test_reloading_customers_is_an_idempotent_upsert(client, engine):
    _upload(client, CUST_ID, "customers.csv")
    csv_bytes = b"customer_id,customer_name\nC0001,Renamed Person\nC5000,New Person\n"
    r = _upload(client, CUST_ID, "update.csv", csv_bytes)
    assert r.status_code == 201
    assert _scalar(engine, "SELECT count(*) FROM customers") == 201
    assert _scalar(engine, "SELECT customer_name FROM customers WHERE customer_id='C0001'") == "Renamed Person"


def test_customers_duplicate_ids_in_file_keep_last_with_warning(client, engine):
    r = _upload(client, CUST_ID, "c.csv", b"customer_id,customer_name\nC1,First\nC1,Second\nC2,Other\n")
    assert r.status_code == 201
    assert r.json()["total_records"] == 2 and any("duplicate" in w for w in r.json()["warnings"])
    assert _scalar(engine, "SELECT customer_name FROM customers WHERE customer_id='C1'") == "Second"


def test_customers_with_blank_id_is_rejected_atomically(client, engine):
    r = _upload(client, CUST_ID, "c.csv", b"customer_id,customer_name\nC1,A\n ,B\n")
    assert r.status_code == 422 and "blank customer_id" in r.json()["detail"]
    assert _scalar(engine, "SELECT count(*) FROM customers") == 0      # nothing partially loaded
    assert _scalar(engine, "SELECT count(*) FROM ingestion_batches") == 0


def test_header_only_file_creates_empty_batch_with_warning(client):
    r = _upload(client, TXN_ID, "empty.csv", (HEADER + "\n").encode())
    assert r.status_code == 201
    assert r.json()["total_records"] == 0 and any("no data rows" in w for w in r.json()["warnings"])


def test_bom_and_crlf_files_are_accepted(client):
    body = b"\xef\xbb\xbf" + (HEADER + "\r\nT1,C1,5.00,2025-01-01,Shop,PENDING\r\n").encode()
    r = _upload(client, TXN_ID, "excel.csv", body)
    assert r.status_code == 201 and r.json()["total_records"] == 1


# ------------------------------------------------------------------ duplicate detection

def test_identical_file_is_rejected_with_existing_batch_id_unless_forced(client, engine):
    first = _upload(client, TXN_ID, "sample_transactions_clean.csv").json()
    again = _upload(client, TXN_ID, "sample_transactions_clean.csv")
    assert again.status_code == 409
    assert again.json()["existing_batch_id"] == first["batch_id"]
    assert again.json()["code"] == "duplicate_file"
    assert _scalar(engine, "SELECT count(*) FROM ingestion_batches") == 1

    forced = _upload(client, TXN_ID, "sample_transactions_clean.csv", force="true")
    assert forced.status_code == 201 and forced.json()["batch_id"] != first["batch_id"]
    assert _scalar(engine, "SELECT count(*) FROM transactions") == 2000


def test_same_content_in_a_different_dataset_is_not_a_duplicate(client):
    body = b"customer_id,customer_name\nC1,A\n"
    assert _upload(client, CUST_ID, "a.csv", body).status_code == 201
    # same bytes can't be valid transactions, so just confirm the dataset scoping of the check
    r = _upload(client, TXN_ID, "a.csv", body)
    assert r.status_code == 422 and r.json()["code"] == "missing_columns"


# ------------------------------------------------------------------ rejections (and their audit trail)

def _failed_audits(engine):
    with engine.connect() as c:
        return c.execute(text("SELECT details FROM audit_logs WHERE action='INGEST' AND execution_status='FAILED'")).scalars().all()


def test_missing_required_columns_is_422_atomic_and_audited(client, engine):
    r = _upload(client, TXN_ID, "t.csv", b"transaction_id,merchant\n1,x\n")
    assert r.status_code == 422 and r.json()["code"] == "missing_columns"
    assert "customer_id" in r.json()["missing"]
    assert _scalar(engine, "SELECT count(*) FROM ingestion_batches") == 0
    assert _scalar(engine, "SELECT count(*) FROM transactions") == 0
    assert list(Path(config.get_settings().upload_dir).glob("*")) == []   # no orphan file left behind
    failed = _failed_audits(engine)
    assert len(failed) == 1 and failed[0]["kind"] == "missing_columns" and failed[0]["source_file"] == "t.csv"


def test_wrong_delimiter_gets_a_helpful_hint(client):
    r = _upload(client, TXN_ID, "semi.csv", HEADER.replace(",", ";").encode() + b"\nT1;C1;5;2025-01-01;x;PENDING\n")
    assert r.status_code == 422 and "delimiter" in r.json()["detail"]


@pytest.mark.parametrize("name,ctype,body,status", [
    ("data.xlsx", "text/csv", b"a,b\n1,2\n", 415),
    ("data.csv", "application/pdf", b"a,b\n1,2\n", 415),
    ("data.csv", "text/csv", b"%PDF-1.4\x00\x00binary", 415),
    ("data.csv", "text/csv", b"", 422),
    ("data.csv", "text/csv", "café,x\n1,2\n".encode("latin-1"), 422),
    ("data.csv", "text/csv", (HEADER + "\nT1,C1,5\n").encode(), 422),
])
def test_unsupported_or_malformed_files_are_rejected_with_clear_status(client, engine, name, ctype, body, status):
    r = client.post(f"/api/v1/datasets/{TXN_ID}/batches", files={"file": (name, body, ctype)})
    assert r.status_code == status, r.text
    assert r.json()["detail"]
    assert _scalar(engine, "SELECT count(*) FROM ingestion_batches") == 0
    assert len(_failed_audits(engine)) == 1


def test_oversized_upload_is_413(client, engine, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "max_upload_mb", 0)
    r = _upload(client, TXN_ID, "sample_transactions.csv")
    assert r.status_code == 413
    assert _scalar(engine, "SELECT count(*) FROM ingestion_batches") == 0


def test_bad_uploaded_by_is_rejected(client):
    r = _upload(client, TXN_ID, "sample_transactions_clean.csv", uploaded_by="x'; DROP TABLE batches;--")
    assert r.status_code == 400


def test_sql_injection_in_filename_is_stored_as_inert_text(client, engine):
    name = "x'); DROP TABLE transactions;--.csv"
    r = client.post(f"/api/v1/datasets/{TXN_ID}/batches",
                    files={"file": (name, (HEADER + "\nT1,C1,5,2025-01-01,x,PENDING\n").encode(), "text/csv")})
    assert r.status_code == 201
    assert "'" not in r.json()["source_file"] and ";" not in r.json()["source_file"]   # sanitised
    assert _scalar(engine, "SELECT count(*) FROM transactions") == 1   # table still exists and loaded


def test_path_traversal_filename_is_reduced_to_a_base_name(client):
    r = client.post(f"/api/v1/datasets/{TXN_ID}/batches",
                    files={"file": ("../../etc/passwd.csv", (HEADER + "\nT1,C1,5,2025-01-01,x,PENDING\n").encode(), "text/csv")})
    assert r.status_code == 201 and r.json()["source_file"] == "passwd.csv"


def test_unknown_dataset_is_404_and_not_audited(client, engine):
    r = _upload(client, 999, "sample_transactions_clean.csv")
    assert r.status_code == 404
    assert _scalar(engine, "SELECT count(*) FROM audit_logs") == 0
    assert client.post("/api/v1/datasets/999/preview", files=_files("sample_transactions_clean.csv")).status_code == 404


def test_missing_file_part_is_422(client):
    assert client.post(f"/api/v1/datasets/{TXN_ID}/batches").status_code == 422


# ------------------------------------------------------------------ batch queries

def test_list_and_get_batches(client):
    a = _upload(client, TXN_ID, "sample_transactions_clean.csv").json()
    b = _upload(client, CUST_ID, "customers.csv").json()

    r = client.get("/api/v1/batches")
    assert r.status_code == 200 and r.headers["X-Total-Count"] == "2"
    assert [x["batch_id"] for x in r.json()] == [b["batch_id"], a["batch_id"]]     # newest first

    only_txn = client.get(f"/api/v1/batches?dataset_id={TXN_ID}")
    assert [x["batch_id"] for x in only_txn.json()] == [a["batch_id"]]
    assert only_txn.json()[0]["dataset_name"] == "transactions"

    assert len(client.get("/api/v1/batches?limit=1").json()) == 1
    assert client.get("/api/v1/batches?limit=0").status_code == 422

    one = client.get(f"/api/v1/batches/{a['batch_id']}")
    assert one.status_code == 200 and one.json()["total_records"] == 1000


def test_get_unknown_or_malformed_batch_id(client):
    assert client.get("/api/v1/batches/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/v1/batches/not-a-uuid").status_code == 422
