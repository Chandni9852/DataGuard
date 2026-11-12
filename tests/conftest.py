"""Test fixtures. Integration tests run against a real PostgreSQL given by TEST_DATABASE_URL
(falls back to DATABASE_URL). They are skipped if no database is reachable."""
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

if TEST_DB_URL:
    os.environ["DATABASE_URL"] = TEST_DB_URL
else:
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost:5432/unused")

# Uploaded files go to a throwaway directory, never the project's uploads/ folder.
UPLOAD_TMP = tempfile.mkdtemp(prefix="dataguard-test-uploads-")
os.environ["UPLOAD_DIR"] = UPLOAD_TMP


@pytest.fixture(scope="session")
def engine():
    if not TEST_DB_URL:
        pytest.skip("No TEST_DATABASE_URL / DATABASE_URL set")
    eng = create_engine(TEST_DB_URL, future=True)
    # A configured-but-unreachable database is a real failure, not a skip:
    # silently skipping would let a broken setup look like a passing suite.
    with eng.connect() as c:
        c.execute(text("SELECT 1"))
    # Rebuild schema from the real DDL files so tests exercise what ships.
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    raw = eng.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute((ROOT / "db" / "schema.sql").read_text())
        cur.execute((ROOT / "db" / "seed_rules.sql").read_text())
        raw.commit()
    finally:
        raw.close()
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture()
def clean_tables(engine):
    """Empty all data tables (keeps seeded rules and datasets) and the upload directory."""
    def wipe():
        with engine.begin() as c:
            c.execute(text("TRUNCATE ingestion_batches, customers RESTART IDENTITY CASCADE"))
        for p in Path(UPLOAD_TMP).glob("*"):
            p.unlink()
    wipe()
    yield
    wipe()


@pytest.fixture()
def client(clean_tables):
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def _remove_upload_tmp():
    yield
    shutil.rmtree(UPLOAD_TMP, ignore_errors=True)


@pytest.fixture()
def restore_rules(engine):
    yield
    with engine.begin() as c:
        c.execute(text("TRUNCATE validation_rules RESTART IDENTITY CASCADE"))
    from tests.conftest import ROOT
    raw = engine.raw_connection()
    try:
        raw.cursor().execute((ROOT / "db" / "seed_rules.sql").read_text())
        raw.commit()
    finally:
        raw.close()
