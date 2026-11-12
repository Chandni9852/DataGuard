"""Unit tests for upload safety checks, CSV parsing and schema detection. No database needed."""
import csv
import json
from pathlib import Path

import pytest

from backend.errors import DataGuardError
from backend.services.ingestion import DATASET_SPECS, _column_report, parse_csv
from backend.services.schema_detection import detect_schema
from backend.utils import file_checks

DATA = Path(__file__).resolve().parents[2] / "data"
TXN_HEADER = "transaction_id,customer_id,transaction_amount,transaction_date,merchant,transaction_status"


def _err(fn, *a, **kw) -> DataGuardError:
    with pytest.raises(DataGuardError) as ei:
        fn(*a, **kw)
    return ei.value


# ----------------------------------------------------------- file_checks

@pytest.mark.parametrize("name", ["data.csv", "DATA.CSV", "my file (1).csv"])
def test_csv_extensions_accepted(name):
    file_checks.check_upload_metadata(name, "text/csv")


@pytest.mark.parametrize("name", ["data.xlsx", "data.csv.exe", "data", "", None, "report.pdf"])
def test_non_csv_extensions_rejected(name):
    assert _err(file_checks.check_upload_metadata, name, "text/csv").kind == "unsupported_type"


@pytest.mark.parametrize("ctype", ["image/png", "application/pdf", "application/zip"])
def test_clearly_non_text_content_types_rejected(ctype):
    assert _err(file_checks.check_upload_metadata, "a.csv", ctype).kind == "unsupported_type"


@pytest.mark.parametrize("ctype", [None, "", "text/csv; charset=utf-8", "application/vnd.ms-excel",
                                   "application/octet-stream"])
def test_common_csv_content_types_accepted(ctype):
    file_checks.check_upload_metadata("a.csv", ctype)


def test_size_limit():
    file_checks.check_size(100, 100)
    assert _err(file_checks.check_size, 101, 100).kind == "too_large"


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd.csv", "passwd.csv"),
    ("C:\\Users\\bob\\tx.csv", "tx.csv"),
    ("a\nb\r.csv", "a_b_.csv"),
    ("..hidden.csv", "hidden.csv"),
    ("<script>.csv", "_script_.csv"),
    ("", "upload.csv"),
    (None, "upload.csv"),
])
def test_sanitize_filename(raw, expected):
    assert file_checks.sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_but_keeps_extension():
    out = file_checks.sanitize_filename("x" * 500 + ".csv")
    assert len(out) <= 200 and out.endswith(".csv")


def test_decode_rejects_empty_binary_and_non_utf8():
    assert _err(file_checks.decode_csv_bytes, b"").kind == "empty"
    assert _err(file_checks.decode_csv_bytes, b"  \n \n").kind == "empty"
    assert _err(file_checks.decode_csv_bytes, b"%PDF-1.4\x00\x01binary").kind == "unsupported_type"
    assert _err(file_checks.decode_csv_bytes, "caf\u00e9".encode("latin-1")).kind == "malformed"


def test_decode_strips_utf8_bom():
    assert file_checks.decode_csv_bytes(b"\xef\xbb\xbfa,b\n1,2\n").startswith("a,b")


def test_sha256_is_stable():
    assert file_checks.sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


# ----------------------------------------------------------- parse_csv

def test_parse_keeps_every_value_as_raw_text():
    df, renamed = parse_csv(f"{TXN_HEADER}\nT1,C1,007.50,2025-01-02,  Cafe ,NA\n".encode())
    assert renamed == {}
    row = df.iloc[0].to_dict()
    assert row["transaction_amount"] == "007.50"      # not converted to a number
    assert row["merchant"] == "  Cafe "               # not trimmed
    assert row["transaction_status"] == "NA"          # not turned into NaN


def test_parse_normalises_headers_and_reports_renames():
    df, renamed = parse_csv(b" Transaction ID ,Customer-ID\n1,2\n")
    assert list(df.columns) == ["transaction_id", "customer_id"]
    assert renamed == {" Transaction ID ": "transaction_id", "Customer-ID": "customer_id"}


def test_parse_rejects_duplicate_and_empty_headers():
    assert "Duplicate" in _err(parse_csv, b"a,A\n1,2\n").message
    assert "empty column name" in _err(parse_csv, b"a,\n1,2\n").message
    # pandas would silently rename an exact duplicate to "a.1"; it must be rejected instead
    assert "Duplicate" in _err(parse_csv, b"transaction_id,transaction_id\n1,2\n").message


@pytest.mark.parametrize("body,record", [
    (b"a,b,c\n1,2,3\n4,5\n", 2),            # short row
    (b"a,b,c\n1,2,3,4\n", 1),               # long row
    (b"a,b\n1,2\n\n3,4,5\n", 2),            # blank line is skipped, numbering counts data records
])
def test_parse_reports_ragged_rows_with_record_number(body, record):
    e = _err(parse_csv, body)
    assert e.kind == "malformed" and e.details["record"] == record


def test_parse_header_only_gives_zero_rows():
    df, _ = parse_csv(b"a,b\n")
    assert len(df) == 0 and list(df.columns) == ["a", "b"]


def test_parse_handles_quoted_commas_and_newlines():
    df, _ = parse_csv(b'a,b\n"x, y","line1\nline2"\n')
    assert df.iloc[0].tolist() == ["x, y", "line1\nline2"]


# ----------------------------------------------------------- column report

def test_column_report_flags_missing_and_unexpected():
    df, _ = parse_csv(b"transaction_id,customer_id,extra_col\n1,2,3\n")
    missing, unexpected, warnings = _column_report(df, DATASET_SPECS["transactions"])
    assert missing == ["transaction_amount", "transaction_date", "merchant", "transaction_status"]
    assert unexpected == ["extra_col"]
    assert any("extra_col" in w for w in warnings)


def test_column_report_all_present():
    df, _ = parse_csv(f"{TXN_HEADER}\n".encode())
    assert _column_report(df, DATASET_SPECS["transactions"])[0] == []


# ----------------------------------------------------------- schema detection

def _profile(filename):
    df, _ = parse_csv((DATA / filename).read_bytes())
    return {c["name"]: c for c in detect_schema(df)}


def test_clean_file_types_are_detected_with_full_confidence():
    p = _profile("sample_transactions_clean.csv")
    assert p["transaction_amount"]["inferred_type"] == "decimal" and p["transaction_amount"]["type_confidence"] == 1.0
    assert p["transaction_date"]["inferred_type"] == "date" and p["transaction_date"]["type_confidence"] == 1.0
    for col in ("transaction_id", "customer_id", "merchant", "transaction_status"):
        assert p[col]["inferred_type"] == "string"
    assert all(c["null_count"] == 0 for c in p.values())


def test_corrupted_file_keeps_types_but_confidence_reveals_bad_values():
    p = _profile("sample_transactions.csv")
    assert p["transaction_amount"]["inferred_type"] == "decimal"
    assert p["transaction_date"]["inferred_type"] == "date"
    assert p["transaction_amount"]["type_confidence"] < 1.0
    assert p["transaction_date"]["type_confidence"] < 1.0


def test_null_counts_match_the_answer_key_per_column():
    """Blank counts from schema detection must equal the generator's completeness failures."""
    key = json.loads((DATA / "sample_transactions_answer_key.json").read_text())
    p = _profile("sample_transactions.csv")
    by_col = {"transaction_id": "C01", "customer_id": "C02", "transaction_amount": "C03",
              "transaction_date": "C04", "transaction_status": "C05", "merchant": "C06"}
    for col, code in by_col.items():
        assert p[col]["null_count"] == key["expected_failures_by_rule"][code], col


def test_detection_on_empty_frame_and_all_blank_column():
    df, _ = parse_csv(b"a,b\n")
    assert [c["null_count"] for c in detect_schema(df)] == [0, 0]
    df, _ = parse_csv(b"a,b\n,1\n ,2\n")
    a = {c["name"]: c for c in detect_schema(df)}["a"]
    assert a["null_count"] == 2 and a["inferred_type"] == "string" and a["distinct_count"] == 0


def test_sample_values_are_distinct_and_limited():
    df, _ = parse_csv(b"a\nx\nx\ny\nz\nw\n")
    assert detect_schema(df)[0]["sample_values"] == ["x", "y", "z"]


def test_customers_sample_file_parses_against_its_spec():
    df, _ = parse_csv((DATA / "customers.csv").read_bytes())
    assert _column_report(df, DATASET_SPECS["customers"])[0] == []
    assert len(df) == 200
