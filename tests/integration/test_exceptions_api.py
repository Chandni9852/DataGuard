"""End-to-end tests for exception management, checked against the generator's answer key."""
import csv
import io
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.services.exceptions import CSV_HEADER

DATA = Path(__file__).resolve().parents[2] / "data"
TXN_ID, CUST_ID = 1, 2
KEY = json.loads((DATA / "sample_transactions_answer_key.json").read_text())
BY_RULE = KEY["expected_failures_by_rule"]
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


@pytest.fixture()
def validated(client):
    _up(client, CUST_ID, "customers.csv")
    bid = _up(client, TXN_ID, "sample_transactions.csv")
    _validate(client, bid)
    return bid


def _list(client, **params):
    r = client.get(f"{API}/exceptions", params={"limit": 500, **params})
    assert r.status_code == 200, r.text
    return r.json(), int(r.headers["X-Total-Count"])


def _severity_by_rule(client):
    return {r["rule_code"]: r["severity"] for r in client.get(f"{API}/rules").json()}


def _review(client, rid, status="REVIEWED", reviewer="alice", comments=None):
    body = {"status": status, "reviewer": reviewer}
    if comments is not None:
        body["comments"] = comments
    return client.post(f"{API}/exceptions/{rid}/review", json=body)


# --------------------------------------------------------------------- listing and filters

def test_lists_every_failure_with_full_detail(client, validated):
    rows, total = _list(client, batch_id=validated)
    assert total == TOTAL == len(rows)
    r = rows[0]
    for field in ("result_id", "batch_id", "record_identifier", "row_number", "rule_code", "rule_name", "category",
                  "severity", "column_name", "offending_value", "error_description", "detected_at", "review_status"):
        assert field in r
    assert r["review_status"] == "OPEN" and r["reviewer"] is None and r["source_file"] == "sample_transactions.csv"


def test_default_order_is_most_severe_first(client, validated):
    rows, _ = _list(client, batch_id=validated)
    rank = {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4}
    ranks = [rank[r["severity"]] for r in rows]
    assert ranks == sorted(ranks) and rows[0]["severity"] == "CRITICAL"


@pytest.mark.parametrize("category", ["COMPLETENESS", "UNIQUENESS", "VALIDITY", "CONSISTENCY", "REFERENTIAL_INTEGRITY"])
def test_category_filter_matches_the_answer_key(client, validated, category):
    rows, total = _list(client, category=category)
    assert total == KEY["expected_failures_by_category"][category] == len(rows)
    assert {r["category"] for r in rows} == {category}


@pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
def test_severity_filter_matches_the_answer_key(client, validated, severity):
    sev = _severity_by_rule(client)
    expected = sum(n for code, n in BY_RULE.items() if sev[code] == severity)
    rows, total = _list(client, severity=severity)
    assert total == expected == len(rows) and {r["severity"] for r in rows} <= {severity}


def test_rule_filter_and_combined_filters(client, validated):
    assert _list(client, rule_code="C02")[1] == BY_RULE["C02"]
    rows, total = _list(client, category="VALIDITY", rule_code="V02", severity="HIGH")
    assert total == BY_RULE["V02"] and {r["rule_code"] for r in rows} == {"V02"}
    assert _list(client, category="COMPLETENESS", rule_code="V02")[1] == 0       # contradictory filters: empty, not error
    assert _list(client, rule_code="NOPE")[1] == 0


def test_filtering_by_batch_isolates_batches(client, validated):
    clean = _up(client, TXN_ID, "sample_transactions_clean.csv")
    _validate(client, clean)
    assert _list(client, batch_id=clean)[1] == 0
    assert _list(client, batch_id=validated)[1] == TOTAL
    assert _list(client)[1] == TOTAL


def test_unknown_batch_is_404_and_bad_enum_is_422(client, validated):
    zero = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"{API}/exceptions", params={"batch_id": zero}).status_code == 404
    assert client.get(f"{API}/exceptions/summary", params={"batch_id": zero}).status_code == 404
    assert client.get(f"{API}/exceptions", params={"category": "BOGUS"}).status_code == 422
    assert client.get(f"{API}/exceptions", params={"severity": "urgent"}).status_code == 422
    assert client.get(f"{API}/exceptions", params={"review_status": "DONE"}).status_code == 422
    assert client.get(f"{API}/exceptions", params={"sort": "random"}).status_code == 422


# --------------------------------------------------------------------- search by record ID

def _expected_matches(fragment: str) -> int:
    return sum(len(r["expected"]) for r in KEY["rows"] if fragment.lower() in r["transaction_id"].lower())


def test_search_by_record_id_is_partial_and_case_insensitive(client, validated):
    dup = next(r["transaction_id"] for r in KEY["rows"] if r["transaction_id"] and
               any(e["rule_code"] == "U01" for e in r["expected"]))
    for fragment in (dup, dup.lower(), dup[1:], dup[:-1]):
        rows, total = _list(client, q=fragment)
        assert total == _expected_matches(fragment) > 0, fragment
        assert all(fragment.lower() in r["record_identifier"].lower() for r in rows)


def test_search_treats_wildcards_literally(client, validated):
    assert _list(client, q="%")[1] == 0
    assert _list(client, q="_")[1] == 0
    assert _list(client, q="T0000")[1] == _expected_matches("T0000") > 0
    assert _list(client, q="nothing-like-this")[1] == 0


def test_records_with_no_id_are_listed_but_not_searchable(client, validated):
    rows, _ = _list(client, rule_code="C01")
    assert len(rows) == BY_RULE["C01"] and all(r["record_identifier"] is None for r in rows)
    assert all(r["row_number"] for r in rows)                       # still locatable by source row


def test_search_with_injection_text_is_harmless(client, validated):
    assert _list(client, q="x'; DROP TABLE validation_results;--")[1] == 0
    assert _list(client)[1] == TOTAL


# --------------------------------------------------------------------- pagination and sorting

def test_pagination_covers_everything_without_overlap(client, validated):
    seen, offset = [], 0
    while True:
        r = client.get(f"{API}/exceptions", params={"limit": 25, "offset": offset})
        assert int(r.headers["X-Total-Count"]) == TOTAL
        page = r.json()
        if not page:
            break
        seen += [x["result_id"] for x in page]
        offset += 25
    assert len(seen) == TOTAL == len(set(seen))


def test_pagination_bounds(client, validated):
    assert client.get(f"{API}/exceptions", params={"limit": 0}).status_code == 422
    assert client.get(f"{API}/exceptions", params={"limit": 501}).status_code == 422
    assert client.get(f"{API}/exceptions", params={"offset": -1}).status_code == 422
    assert client.get(f"{API}/exceptions", params={"offset": 10_000}).json() == []


def test_sort_options(client, validated):
    by_row, _ = _list(client, sort="row")
    rows_ = [r["row_number"] for r in by_row]
    assert rows_ == sorted(rows_)
    by_rule, _ = _list(client, sort="rule")
    codes = [r["rule_code"] for r in by_rule]
    assert codes == sorted(codes)
    assert _list(client, sort="detected")[1] == TOTAL


# --------------------------------------------------------------------- detail

def test_detail_shows_the_raw_source_record_and_empty_history(client, validated):
    rows, _ = _list(client, rule_code="V03")
    d = client.get(f"{API}/exceptions/{rows[0]['result_id']}").json()
    with (DATA / "sample_transactions.csv").open(newline="", encoding="utf-8") as f:
        source = list(csv.DictReader(f))[d["row_number"] - 1]
    for col, value in source.items():
        assert d["record"][col] == (value if value != "" else None)
    assert d["record"]["source_row_number"] == d["row_number"]
    assert d["offending_value"] == source["transaction_date"] and d["history"] == []
    assert d["review_status"] == "OPEN"


def test_detail_and_history_for_unknown_ids(client, validated):
    assert client.get(f"{API}/exceptions/999999").status_code == 404
    assert client.get(f"{API}/exceptions/999999/history").status_code == 404
    assert client.get(f"{API}/exceptions/abc").status_code == 422
    assert _review(client, 999999).status_code == 404


# --------------------------------------------------------------------- review workflow

def test_marking_reviewed_records_reviewer_comment_and_time(client, validated):
    rid = _list(client)[0][0]["result_id"]
    d = _review(client, rid, "REVIEWED", "alice", "Confirmed with the source team").json()
    assert d["review_status"] == "REVIEWED" and d["reviewer"] == "alice"
    assert d["last_comment"] == "Confirmed with the source team" and d["reviewed_at"]
    assert len(d["history"]) == 1 and d["history"][0]["review_status"] == "REVIEWED"


def test_history_is_append_only_and_newest_first(client, engine, validated):
    rid = _list(client)[0][0]["result_id"]
    _review(client, rid, "IN_REVIEW", "alice")
    first_row = _q(engine, "SELECT * FROM exception_reviews WHERE result_id=:r", r=rid)[0]
    _review(client, rid, "REVIEWED", "bob", "looks fine")
    _review(client, rid, "OPEN", "carol", "reopened: new evidence")
    hist = client.get(f"{API}/exceptions/{rid}/history").json()
    assert [(h["review_status"], h["reviewer"]) for h in hist] == [("OPEN", "carol"), ("REVIEWED", "bob"), ("IN_REVIEW", "alice")]
    rows = _q(engine, "SELECT * FROM exception_reviews WHERE result_id=:r ORDER BY review_id", r=rid)
    assert len(rows) == 3 and rows[0] == first_row                   # the earliest decision was never changed
    assert client.get(f"{API}/exceptions/{rid}").json()["review_status"] == "OPEN"


@pytest.mark.parametrize("body,status", [
    ({"status": "WAIVED", "reviewer": "alice"}, 422),                       # waiving needs a justification
    ({"status": "WAIVED", "reviewer": "alice", "comments": "   "}, 422),
    ({"status": "REVIEWED", "reviewer": ""}, 422),                          # reviewer is mandatory
    ({"status": "REVIEWED", "reviewer": "   "}, 422),
    ({"status": "REVIEWED"}, 422),
    ({"status": "DONE", "reviewer": "alice"}, 422),
    ({"status": "REVIEWED", "reviewer": "x'; DROP TABLE y;--"}, 400),
    ({"status": "REVIEWED", "reviewer": "alice", "comments": "x" * 2001}, 422),
    ({"status": "REVIEWED", "reviewer": "a" * 101}, 422),
])
def test_invalid_reviews_are_refused_and_change_nothing(client, engine, validated, body, status):
    rid = _list(client)[0][0]["result_id"]
    assert client.post(f"{API}/exceptions/{rid}/review", json=body).status_code == status
    assert _q(engine, "SELECT count(*) FROM exception_reviews")[0][0] == 0
    assert _q(engine, "SELECT count(*) FROM audit_logs WHERE action='EXCEPTION_REVIEW'")[0][0] == 0


def test_waiving_and_reopening_require_comments_but_other_statuses_do_not(client, validated):
    rid = _list(client)[0][0]["result_id"]
    assert _review(client, rid, "IN_REVIEW").status_code == 200
    assert _review(client, rid, "WAIVED", comments="Accepted risk, ticket DG-12").status_code == 200
    assert _review(client, rid, "OPEN").status_code == 422
    assert _review(client, rid, "OPEN", comments="Waiver withdrawn").status_code == 200


def test_repeating_the_same_status_needs_a_comment(client, validated):
    rid = _list(client)[0][0]["result_id"]
    _review(client, rid, "REVIEWED")
    r = _review(client, rid, "REVIEWED")
    assert r.status_code == 409 and "already REVIEWED" in r.json()["detail"]
    assert _review(client, rid, "REVIEWED", comments="Second check done").status_code == 200
    assert len(client.get(f"{API}/exceptions/{rid}/history").json()) == 2


def test_review_status_filter_and_summary_follow_the_decisions(client, validated):
    ids = [r["result_id"] for r in _list(client, category="COMPLETENESS")[0][:3]]
    for rid in ids:
        _review(client, rid, "REVIEWED")
    _review(client, _list(client, category="VALIDITY")[0][0]["result_id"], "IN_REVIEW")
    assert _list(client, review_status="REVIEWED")[1] == 3
    assert _list(client, review_status="IN_REVIEW")[1] == 1
    assert _list(client, review_status="OPEN")[1] == TOTAL - 4
    assert _list(client, review_status="REVIEWED", category="VALIDITY")[1] == 0
    s = client.get(f"{API}/exceptions/summary").json()
    assert s["total"] == TOTAL
    assert s["by_review_status"] == {"OPEN": TOTAL - 4, "IN_REVIEW": 1, "REVIEWED": 3, "WAIVED": 0}
    reviewed = client.get(f"{API}/exceptions/summary", params={"review_status": "REVIEWED"}).json()
    assert reviewed["total"] == 3 and reviewed["by_category"]["COMPLETENESS"] == 3


def test_summary_matches_the_answer_key(client, validated):
    s = client.get(f"{API}/exceptions/summary", params={"batch_id": validated}).json()
    assert s["total"] == TOTAL and s["by_category"] == {
        "COMPLETENESS": 0, "UNIQUENESS": 0, "VALIDITY": 0, "CONSISTENCY": 0, "REFERENTIAL_INTEGRITY": 0,
        **KEY["expected_failures_by_category"]}
    sev = _severity_by_rule(client)
    assert sum(s["by_severity"].values()) == TOTAL
    assert s["by_severity"]["CRITICAL"] == sum(n for c, n in BY_RULE.items() if sev[c] == "CRITICAL")
    assert s["by_review_status"]["OPEN"] == TOTAL


def test_reviews_never_touch_source_records_or_failures(client, engine, validated):
    snap = lambda: (_q(engine, "SELECT * FROM transactions ORDER BY record_id"),  # noqa: E731
                    _q(engine, "SELECT * FROM validation_results ORDER BY result_id"))
    before = snap()
    ids = [r["result_id"] for r in _list(client)[0][:10]]
    for rid in ids[:5]:
        _review(client, rid, "REVIEWED", comments="ok")
    client.post(f"{API}/exceptions/review-bulk", json={"result_ids": ids, "status": "WAIVED", "reviewer": "a", "comments": "x"})
    assert snap() == before


def test_review_decisions_are_audited(client, engine, validated):
    rid = _list(client)[0][0]["result_id"]
    _review(client, rid, "REVIEWED", "alice", "fine")
    a = _q(engine, "SELECT * FROM audit_logs WHERE action='EXCEPTION_REVIEW'")
    assert len(a) == 1 and a[0].actor == "alice" and str(a[0].batch_id) == validated
    assert a[0].details["new_status"] == "REVIEWED" and a[0].details["result_ids"] == [rid]
    assert a[0].details["previous_statuses"] == ["OPEN"] and "fine" not in json.dumps(a[0].details)


# --------------------------------------------------------------------- bulk review

def test_bulk_review_applies_one_decision_and_skips_unchanged(client, engine, validated):
    ids = [r["result_id"] for r in _list(client)[0][:8]]
    r = client.post(f"{API}/exceptions/review-bulk", json={"result_ids": ids[:5], "status": "REVIEWED", "reviewer": "alice"})
    assert r.status_code == 200 and r.json() == {"updated": 5, "skipped_unchanged": 0, "result_ids": sorted(ids[:5])}
    r = client.post(f"{API}/exceptions/review-bulk", json={"result_ids": ids + ids[:2], "status": "REVIEWED", "reviewer": "bob"})
    assert r.json()["updated"] == 3 and r.json()["skipped_unchanged"] == 5          # duplicates in the request are deduplicated
    assert _list(client, review_status="REVIEWED")[1] == 8
    assert _q(engine, "SELECT count(*) FROM exception_reviews")[0][0] == 8
    audits = _q(engine, "SELECT records_processed, details FROM audit_logs WHERE action='EXCEPTION_REVIEW' ORDER BY log_id")
    assert [a.records_processed for a in audits] == [5, 3] and audits[1].details["skipped_unchanged"] == 5


def test_bulk_with_an_unknown_id_changes_nothing(client, engine, validated):
    ids = [r["result_id"] for r in _list(client)[0][:3]]
    r = client.post(f"{API}/exceptions/review-bulk", json={"result_ids": ids + [999999], "status": "REVIEWED", "reviewer": "alice"})
    assert r.status_code == 404 and r.json()["missing"] == [999999]
    assert _q(engine, "SELECT count(*) FROM exception_reviews")[0][0] == 0
    assert _q(engine, "SELECT count(*) FROM audit_logs WHERE action='EXCEPTION_REVIEW'")[0][0] == 0


@pytest.mark.parametrize("body", [
    {"result_ids": [], "status": "REVIEWED", "reviewer": "a"},
    {"result_ids": list(range(1, 502)), "status": "REVIEWED", "reviewer": "a"},
    {"result_ids": [1], "status": "WAIVED", "reviewer": "a"},
    {"result_ids": [1], "status": "REVIEWED", "reviewer": " "},
])
def test_bulk_rejects_bad_requests(client, validated, body):
    assert client.post(f"{API}/exceptions/review-bulk", json=body).status_code == 422


def test_bulk_review_of_everything_matching_a_filter(client, validated):
    ids = [r["result_id"] for r in _list(client, category="COMPLETENESS")[0]]
    r = client.post(f"{API}/exceptions/review-bulk", json={"result_ids": ids, "status": "WAIVED", "reviewer": "lead",
                                                           "comments": "Known gap in legacy feed"})
    assert r.json()["updated"] == KEY["expected_failures_by_category"]["COMPLETENESS"]
    assert _list(client, review_status="WAIVED", category="COMPLETENESS")[1] == len(ids)


# --------------------------------------------------------------------- CSV export

def _csv(client, **params):
    r = client.get(f"{API}/reports/exceptions.csv", params=params)
    assert r.status_code == 200, r.text
    return r, list(csv.reader(io.StringIO(r.text)))


def test_csv_contains_every_exception_with_the_expected_columns(client, validated):
    r, rows = _csv(client, batch_id=validated)
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith('attachment; filename="dataguard_exceptions_')
    assert rows[0] == CSV_HEADER and len(rows) - 1 == TOTAL
    body = [dict(zip(CSV_HEADER, row)) for row in rows[1:]]
    assert {b["batch_id"] for b in body} == {validated}
    from collections import Counter
    assert Counter(b["rule_code"] for b in body) == BY_RULE
    assert body[0]["severity"] == "CRITICAL"                              # same order as the list view
    listed = [x["result_id"] for x in _list(client, batch_id=validated)[0]]
    assert [int(b["result_id"]) for b in body] == listed


def test_csv_respects_filters_and_shows_review_status(client, validated):
    ids = [r["result_id"] for r in _list(client, rule_code="C06")[0][:2]]
    for rid in ids:
        _review(client, rid, "REVIEWED", "alice", "approved")
    _, rows = _csv(client, rule_code="C06")
    body = [dict(zip(CSV_HEADER, r)) for r in rows[1:]]
    assert len(body) == BY_RULE["C06"]
    reviewed = [b for b in body if b["review_status"] == "REVIEWED"]
    assert len(reviewed) == 2 and {b["reviewer"] for b in reviewed} == {"alice"} and reviewed[0]["last_comment"] == "approved"
    assert len(_csv(client, rule_code="C06", review_status="REVIEWED")[1]) - 1 == 2
    assert len(_csv(client, q="nothing-like-this")[1]) == 1                # header only


def test_csv_export_is_audited_and_unknown_batch_is_404(client, engine, validated):
    _csv(client, batch_id=validated, category="VALIDITY")
    a = _q(engine, "SELECT details FROM audit_logs WHERE action='EXPORT_EXCEPTIONS'")
    assert len(a) == 1 and a[0].details["filters"]["category"] == "VALIDITY"
    assert client.get(f"{API}/reports/exceptions.csv", params={"batch_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404
    assert client.get(f"{API}/reports/exceptions.csv", params={"category": "BOGUS"}).status_code == 422


def test_csv_neutralises_formula_injection_but_keeps_numbers_exact(client):
    _up(client, CUST_ID, "customers.csv")
    rows = [
        "T1,C0001,10.00,2026-01-01,Shop,=cmd|' /C calc'!A0",     # invalid status that looks like a formula
        "T2,C0001,-5.00,2026-01-01,Shop,COMPLETED",              # negative amount: plain number, must stay exact
        "T3,C0001,-1+1,2026-01-01,Shop,COMPLETED",               # non-numeric amount that starts with '-'
        "T4,C0001,@SUM(1+1),2026-01-01,Shop,COMPLETED",
    ]
    bid = _up(client, TXN_ID, "evil.csv", (HEADER + "\n".join(rows) + "\n").encode())
    _validate(client, bid)
    for rid in [r["result_id"] for r in _list(client, batch_id=bid)[0] if r["rule_code"] == "V04"]:
        _review(client, rid, "REVIEWED", "alice", "=HYPERLINK(\"http://evil\",\"x\")")
    _, out = _csv(client, batch_id=bid)
    body = [dict(zip(CSV_HEADER, r)) for r in out[1:]]
    offending = {b["rule_code"] + ":" + b["record_identifier"]: b["offending_value"] for b in body}
    assert offending["V04:T1"] == "'=cmd|' /C calc'!A0"
    assert offending["V02:T2"] == "-5.00"                                  # untouched
    assert offending["V01:T3"] == "'-1+1" and offending["V01:T4"] == "'@SUM(1+1)"
    assert next(b for b in body if b["rule_code"] == "V04")["last_comment"].startswith("'=HYPERLINK")
    assert all(not c.startswith(("=", "+", "@")) for row in out for c in row)


def test_large_export_streams_across_several_chunks(client):
    _up(client, CUST_ID, "customers.csv")
    rows = [f"S{i},C0001,abc,2026-01-01,Shop,COMPLETED" for i in range(4500)]    # 4500 > two 2000-row chunks
    bid = _up(client, TXN_ID, "many.csv", (HEADER + "\n".join(rows) + "\n").encode())
    _validate(client, bid)
    _, out = _csv(client, batch_id=bid)
    assert len(out) - 1 == 4500 and len({r[0] for r in out[1:]}) == 4500
