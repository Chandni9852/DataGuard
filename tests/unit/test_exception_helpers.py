"""Unit tests for the pure helpers in the exception service. No database needed."""
import pytest

from backend.services.exceptions import csv_safe, escape_like


@pytest.mark.parametrize("raw,escaped", [
    ("T0001", "T0001"), ("50%", "50\\%"), ("a_b", "a\\_b"), ("back\\slash", "back\\\\slash"),
    ("%_\\", "\\%\\_\\\\"), ("", ""),
])
def test_escape_like_neutralises_wildcards(raw, escaped):
    assert escape_like(raw) == escaped


@pytest.mark.parametrize("value,expected", [
    (None, ""), ("", ""), ("plain text", "plain text"), (123, "123"),
    # formula triggers are defused with a leading apostrophe
    ("=1+1", "'=1+1"), ("+cmd", "'+cmd"), ("@SUM(A1)", "'@SUM(A1)"), ("-1+1", "'-1+1"),
    ("\tpayload", "'\tpayload"), ("\rpayload", "'\rpayload"),
    ("=HYPERLINK(\"http://x\",\"y\")", "'=HYPERLINK(\"http://x\",\"y\")"),
    # plain numbers cannot be formulas and must stay exactly as stored (evidence)
    ("-5.00", "-5.00"), ("-12", "-12"), ("0.00", "0.00"), ("10", "10"),
    # a minus sign that is not a plain number is treated as text
    ("-", "'-"), ("-abc", "'-abc"), ("--5", "'--5"), ("-5.", "'-5."), ("-.5", "'-.5"),
    # only the first character matters
    ("a=b", "a=b"), (" =1", " =1"),
])
def test_csv_safe(value, expected):
    assert csv_safe(value) == expected
