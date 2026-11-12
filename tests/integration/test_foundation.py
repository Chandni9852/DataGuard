import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from backend.models import Dataset, IngestionBatch, Transaction, ValidationRule

EXPECTED_TABLES = {
    "datasets", "ingestion_batches", "customers", "transactions", "validation_rules",
    "validation_results", "quality_metrics", "audit_logs", "exception_reviews",
}


def test_all_tables_exist(db):
    rows = db.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    )).scalars().all()
    assert EXPECTED_TABLES <= set(rows)


def test_rules_seeded_with_all_categories(db):
    rules = db.execute(select(ValidationRule)).scalars().all()
    assert len(rules) == 15
    assert {r.rule_category for r in rules} == {
        "COMPLETENESS", "UNIQUENESS", "VALIDITY", "CONSISTENCY", "REFERENTIAL_INTEGRITY"}
    assert all(r.is_active for r in rules)


def test_seed_is_rerunnable(engine):
    from tests.conftest import ROOT
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute((ROOT / "db" / "seed_rules.sql").read_text())
        raw.commit()
    finally:
        raw.close()
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM validation_rules")).scalar() == 15
        assert c.execute(text("SELECT count(*) FROM datasets")).scalar() == 2


def test_invalid_processing_status_rejected(db):
    ds = db.execute(select(Dataset).where(Dataset.dataset_name == "transactions")).scalar_one()
    db.add(IngestionBatch(dataset_id=ds.dataset_id, source_file="a.csv", file_name="x.csv",
                          processing_status="BOGUS"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_duplicate_and_null_transaction_ids_are_storable(db):
    """Raw-first design: bad data must be storable so it can be reported."""
    ds = db.execute(select(Dataset).where(Dataset.dataset_name == "transactions")).scalar_one()
    b = IngestionBatch(batch_id=uuid.uuid4(), dataset_id=ds.dataset_id, source_file="a.csv", file_name="x.csv")
    db.add(b)
    db.flush()
    db.add_all([
        Transaction(batch_id=b.batch_id, source_row_number=1, transaction_id="T1", transaction_amount="abc"),
        Transaction(batch_id=b.batch_id, source_row_number=2, transaction_id="T1"),
        Transaction(batch_id=b.batch_id, source_row_number=3, transaction_id=None),
    ])
    db.flush()
    assert db.execute(text("SELECT count(*) FROM transactions WHERE batch_id=:b"), {"b": b.batch_id}).scalar() == 3


def test_health_endpoint(engine):
    from backend.main import app
    client = TestClient(app)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "up"}
