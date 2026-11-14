import streamlit as st

import api_client as api
from components import ui

st.title("Audit & Lineage")
if not ui.require_api():
    st.stop()

tab_audit, tab_batch, tab_exc = st.tabs(["Audit trail", "Batch lineage", "Exception lineage"])

with tab_audit:
    actions = ui.call(api.get, "/audit-logs/actions", default=[])
    batches = ui.call(api.get, "/batches", default=[], limit=200)
    c1, c2, c3, c4 = st.columns(4)
    action = c1.selectbox("Action", ["All"] + actions)
    status = c2.selectbox("Status", ["All", "STARTED", "SUCCESS", "FAILED"])
    actor = c3.text_input("Actor").strip()
    batch = c4.selectbox("Batch", [None] + batches, format_func=lambda b: "All" if b is None else ui.batch_label(b),
                         key="audit_batch")
    page = st.number_input("Page", 1, 1000, 1, key="audit_page")
    rows, total = ui.call(api.get_with_total, "/audit-logs", default=([], 0), action=action, status=status,
                          actor=actor or None, batch_id=batch["batch_id"] if batch else None,
                          limit=ui.PAGE_SIZE, offset=(page - 1) * ui.PAGE_SIZE)
    st.caption(f"{total:,} audit entries (newest first). The audit trail is append-only: entries cannot be edited or deleted.")
    if rows:
        st.dataframe(ui.to_frame(rows, ["log_id", "action_timestamp", "action", "actor", "execution_status",
                                        "source_file", "records_processed", "failure_count", "duration_ms"]),
                     width="stretch", hide_index=True)
        with st.expander("Details of one entry"):
            log_id = st.selectbox("Log ID", [r["log_id"] for r in rows])
            st.json(next(r for r in rows if r["log_id"] == log_id))

with tab_batch:
    b = ui.pick_batch("lin_batch", dataset_id=None)
    if b:
        verify = st.checkbox("Re-hash the stored original file", value=True)
        lin = ui.call(api.get, f"/batches/{b['batch_id']}/lineage", verify_file=verify)
        if lin:
            s, d, ds = lin["source"], lin["destination"], lin["downstream"]
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**Source**")
                st.write(f"File: `{s['original_file_name']}`")
                st.write(f"SHA-256: `{(s['sha256'] or 'n/a')[:16]}…`")
                st.write(f"Stored file: **{s['stored_file_status']}**")
            with c2:
                st.markdown("**Destination**")
                st.write(f"Table: `{d['table']}`")
                st.write(f"Records expected / loaded: {d['records_expected']:,} / "
                         f"{'n/a' if d['records_loaded'] is None else format(d['records_loaded'], ',')}")
                st.write(f"Counts match: **{d['loaded_records_match']}**")
            with c3:
                st.markdown("**Downstream**")
                st.write(f"Validation failures: {ds['validation_failures']:,}")
                st.write("Review status: " + (", ".join(f"{k} {v}" for k, v in ds["exceptions_by_review_status"].items()) or "none"))
                if ds["latest_quality"]:
                    st.write(f"Latest overall score: **{ui.fmt_score(ds['latest_quality']['overall_score'])}**")
            st.markdown("**Processing timeline** (oldest first)")
            st.dataframe(ui.to_frame(lin["timeline"], ["timestamp", "action", "actor", "status",
                                                       "records_processed", "failure_count", "duration_ms"]),
                         width="stretch", hide_index=True)

with tab_exc:
    rid = st.number_input("Exception (result) ID", min_value=1, step=1, value=1)
    if st.button("Trace exception"):
        lin = ui.call(api.get, f"/exceptions/{int(rid)}/lineage")
        if lin:
            e = lin["exception"]
            st.write(f"**{e['rule_code']} {e['rule_name']}** ({e['severity']}): {e['error_description']}")
            st.write(f"Column `{e['column_name']}` · offending value `{e['offending_value']}` · status {e['review_status']}")
            st.markdown(f"**Source row {lin['source_record']['row_number']}**")
            st.json(lin["source_record"]["values"])
            m = lin["matches_original_file"]
            st.write({True: "✅ Matches the original file row.", False: "❌ Differs from the original file row.",
                      None: "Original file not available to compare."}[m])
            b = lin["batch"]
            st.write(f"Batch `{ui.short(b['batch_id'])}` · {b['dataset_name']} · {b['processing_status']} · "
                     f"file `{b['source']['original_file_name']}` ({b['source']['stored_file_status']})")
