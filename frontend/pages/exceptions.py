import streamlit as st

import api_client as api
from components import ui

st.title("Exception Management")
if not ui.require_api():
    st.stop()

# ---- filters -------------------------------------------------------------------------------------
batches = ui.call(api.get, "/batches", default=[], dataset_id=1, limit=200)
with st.container(border=True):
    f1, f2, f3, f4 = st.columns(4)
    batch = f1.selectbox("Batch", [None] + batches, format_func=lambda b: "All batches" if b is None else ui.batch_label(b))
    severity = f2.selectbox("Severity", ["All"] + ui.SEVERITIES)
    category = f3.selectbox("Category", ["All"] + ui.CATEGORIES)
    status = f4.selectbox("Review status", ["All"] + ui.STATUSES)
    g1, g2, g3 = st.columns([1, 1, 1])
    rule_code = g1.text_input("Rule code", placeholder="e.g. V02").strip().upper()
    q = g2.text_input("Record ID contains", placeholder="e.g. T0001").strip()
    sort = g3.selectbox("Sort by", ["severity", "detected", "row", "rule"])

filters = dict(batch_id=batch["batch_id"] if batch else None, severity=severity, category=category,
               review_status=status, rule_code=rule_code or None, q=q or None)

summary = ui.call(api.get, "/exceptions/summary", default=None, **filters)
if summary:
    m = st.columns(5)
    m[0].metric("Matching", f"{summary['total']:,}")
    for col, s in zip(m[1:], ui.STATUSES):
        col.metric(s.replace("_", " ").title(), f"{summary['by_review_status'].get(s, 0):,}")

total = summary["total"] if summary else 0
pages_n = max(1, -(-total // ui.PAGE_SIZE))
page = st.number_input("Page", 1, pages_n, 1, help=f"{ui.PAGE_SIZE} rows per page; {pages_n} pages") if pages_n > 1 else 1
rows, _ = ui.call(api.get_with_total, "/exceptions", default=([], 0), sort=sort, limit=ui.PAGE_SIZE,
                  offset=(page - 1) * ui.PAGE_SIZE, **filters)

if not rows:
    st.info("No exceptions match these filters.")
    st.stop()

cols = ["result_id", "severity", "rule_code", "category", "record_identifier", "row_number", "column_name",
        "offending_value", "error_description", "review_status", "reviewer"]
event = st.dataframe(ui.to_frame(rows, cols), width="stretch", hide_index=True,
                     on_select="rerun", selection_mode="multi-row", key=f"exc_table_{page}")
picked = [rows[i] for i in event.selection.rows] if event and event.selection else []
st.caption("Tick rows on the left to review them. Tick several for a bulk decision (this page only, max 500).")

# ---- review ---------------------------------------------------------------------------------------
if picked:
    st.subheader(f"Review {len(picked)} selected exception(s)")
    with st.form("review_form", clear_on_submit=False):
        r1, r2 = st.columns(2)
        decision = r1.selectbox("Decision", ui.STATUSES, index=2)
        reviewer = r2.text_input("Reviewer (required)", max_chars=100)
        comment = st.text_area("Comment", max_chars=2000, help="Required for WAIVED and for re-opening")
        submitted = st.form_submit_button("Save decision", type="primary")
    if submitted:
        if not reviewer.strip():
            st.error("Reviewer is required.")
        elif len(picked) == 1:
            if ui.call(api.post, f"/exceptions/{picked[0]['result_id']}/review",
                       json={"status": decision, "reviewer": reviewer.strip(), "comments": comment or None}):
                st.success("Decision recorded.")
                st.rerun()
        else:
            res = ui.call(api.post, "/exceptions/review-bulk",
                          json={"result_ids": [p["result_id"] for p in picked], "status": decision,
                                "reviewer": reviewer.strip(), "comments": comment or None})
            if res:
                st.success(f"Updated {res['updated']}, skipped {res['skipped_unchanged']} already in that status.")
                st.rerun()

    if len(picked) == 1:
        rid = picked[0]["result_id"]
        detail = ui.call(api.get, f"/exceptions/{rid}")
        if detail:
            st.subheader(f"Exception {rid}: {detail['rule_code']} {detail['rule_name']}")
            d1, d2 = st.columns(2)
            with d1:
                st.markdown("**Source record (as ingested)**")
                st.json(detail["record"])
            with d2:
                st.markdown("**Review history (newest first)**")
                if detail["history"]:
                    st.dataframe(ui.to_frame(detail["history"], ["reviewed_at", "review_status", "reviewer", "comments"]),
                                 width="stretch", hide_index=True)
                else:
                    st.caption("No reviews yet: status is OPEN.")
            with st.expander("Trace to original file"):
                lin = ui.call(api.get, f"/exceptions/{rid}/lineage")
                if lin:
                    m = lin["matches_original_file"]
                    st.write({True: "✅ Database record matches the original file row.",
                              False: "❌ Database record DIFFERS from the original file row.",
                              None: "Original file not available to compare."}[m])
                    st.write(f"Row {lin['source_record']['row_number']} of `{lin['batch']['source']['original_file_name']}` "
                             f"(file status: {lin['batch']['source']['stored_file_status']})")
