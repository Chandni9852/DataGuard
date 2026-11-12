"""Unit tests for the original-file row lookup used by exception lineage. No database needed."""
import pytest

from backend.services.lineage import original_row


def write(tmp_path, content: bytes):
    p = tmp_path / "f.csv"
    p.write_bytes(content)
    return p


def test_rows_are_numbered_from_one_after_the_header(tmp_path):
    p = write(tmp_path, b"a,b\n1,2\n3,4\n5,6\n")
    assert original_row(p, 1) == {"a": "1", "b": "2"}
    assert original_row(p, 3) == {"a": "5", "b": "6"}


def test_blank_lines_are_skipped_exactly_like_ingestion_does(tmp_path):
    p = write(tmp_path, b"a,b\n1,2\n\n3,4\n\n\n5,6\n")
    assert [original_row(p, n)["a"] for n in (1, 2, 3)] == ["1", "3", "5"]


def test_row_beyond_the_end_is_none(tmp_path):
    assert original_row(write(tmp_path, b"a\n1\n"), 2) is None
    assert original_row(write(tmp_path, b"a\n"), 1) is None


def test_bom_crlf_quotes_and_embedded_newlines(tmp_path):
    p = write(tmp_path, b'\xef\xbb\xbfA,B\r\n"x, y","line1\nline2"\r\n3,4\r\n')
    assert original_row(p, 1) == {"a": "x, y", "b": "line1\nline2"}
    assert original_row(p, 2) == {"a": "3", "b": "4"}


def test_headers_are_normalised_like_ingestion(tmp_path):
    p = write(tmp_path, b" Transaction ID ,Customer-ID\nT1,C1\n")
    assert original_row(p, 1) == {"transaction_id": "T1", "customer_id": "C1"}


def test_whitespace_only_values_are_preserved(tmp_path):
    assert original_row(write(tmp_path, b"a,b\n   ,x\n"), 1) == {"a": "   ", "b": "x"}


def test_invalid_utf8_raises_so_the_caller_can_report_a_mismatch(tmp_path):
    with pytest.raises(UnicodeDecodeError):
        original_row(write(tmp_path, b"a\n\xff\xfe\n"), 1)
