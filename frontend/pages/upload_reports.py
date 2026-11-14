import streamlit as st

import api_client as api
from components import ui

st.title("Upload & Reports")
if not ui.require_api():
    st.stop()

tab_up, tab_val, tab_rep = st.tabs(["Upload CSV", "Run validation", "Download exceptions"])

with tab_up:
    datasets = ui.call(api.get, "/datasets", default=[])
    if not datasets:
        st.stop()
    ds = st.selectbox("Dataset", datasets, format_func=lambda d: d["dataset_name"])
    f = st.file_uploader("CSV file", type=["csv"])
    uploader = st.text_input("Uploaded by", value="dashboard-user", max_chars=100)
    if f is not None:
        content = f.getvalue()
        prev = ui.call(api.upload, f"/datasets/{ds['dataset_id']}/preview", f.name, content)
        if prev:
            st.write(f"**{prev['total_rows']:,}** rows · {prev['size_bytes']:,} bytes · "
                     f"schema {'✅ valid' if prev['schema_valid'] else '❌ invalid'}")
            for w in prev["warnings"]:
                st.warning(w)
            if prev["missing_required_columns"]:
                st.error("Missing required columns: " + ", ".join(prev["missing_required_columns"]))
            st.dataframe(ui.to_frame(prev["preview_rows"]), width="stretch", hide_index=True)
            with st.expander("Column profile"):
                st.dataframe(ui.to_frame(prev["columns"]), width="stretch", hide_index=True)
            force = st.checkbox("Ingest even if an identical file was ingested before")
            validate_after = st.checkbox("Validate and score right after ingesting", value=True)
            if st.button("Ingest file", type="primary", disabled=not prev["schema_valid"]):
                b = ui.call(api.upload, f"/datasets/{ds['dataset_id']}/batches", f.name, content,
                            uploaded_by=uploader or None, force=force)
                if b:
                    st.success(f"Ingested {b['total_records']:,} rows as batch {ui.short(b['batch_id'])}.")
                    for w in b.get("warnings", []):
                        st.warning(w)
                    if validate_after and ds["dataset_name"] == "transactions":
                        v = ui.call(api.post, f"/batches/{b['batch_id']}/validate", json={"requested_by": uploader or None})
                        if v and v.get("quality"):
                            st.success(f"Validated: overall score {ui.fmt_score(v['quality']['overall_score'])}, "
                                       f"{v['total_failures']:,} failures. See the Data Quality page.")
                    elif validate_after:
                        st.info("Validation applies to the transactions dataset; reference data is not validated.")

with tab_val:
    b = ui.pick_batch("val_batch", statuses=("UPLOADED", "FAILED"))
    if b:
        st.write("Runs every active rule and stores the failures. A completed batch cannot be validated again.")
        if st.button("Validate batch", type="primary"):
            v = ui.call(api.post, f"/batches/{b['batch_id']}/validate", json={"requested_by": "dashboard"})
            if v:
                st.success(f"Done: {v['total_failures']:,} failures across {v['invalid_records']:,} records.")

with tab_rep:
    batches = ui.call(api.get, "/batches", default=[], dataset_id=1, limit=200)
    c1, c2, c3 = st.columns(3)
    batch = c1.selectbox("Batch", [None] + batches, format_func=lambda b: "All batches" if b is None else ui.batch_label(b),
                         key="rep_batch")
    sev = c2.selectbox("Severity", ["All"] + ui.SEVERITIES, key="rep_sev")
    stat = c3.selectbox("Review status", ["All"] + ui.STATUSES, key="rep_stat")
    params = dict(batch_id=batch["batch_id"] if batch else None, severity=sev, review_status=stat)
    summary = ui.call(api.get, "/exceptions/summary", default=None, **params)
    n = summary["total"] if summary else 0
    st.write(f"{n:,} exceptions match.")
    if n > 200_000:
        st.warning("That is a large export; narrow the filters (batch, severity) first.")
    elif n and st.button("Prepare CSV"):
        data = ui.call(api.download_csv, default=None, **params)
        if data:
            st.download_button("Download CSV", data, file_name="dataguard_exceptions.csv", mime="text/csv")
            st.caption("Exports are recorded in the audit trail.")
