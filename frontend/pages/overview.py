import plotly.express as px
import streamlit as st

import api_client as api
from components import ui

st.title("DataGuard – Overview")
if not ui.require_api():
    st.stop()

trend = ui.call(api.get, "/metrics/trend", default=[], dataset_id=1, limit=100)
batches, n_batches = ui.call(api.get_with_total, "/batches", default=([], 0), dataset_id=1, limit=200)
summary = ui.call(api.get, "/exceptions/summary", default=None)

latest = trend[-1] if trend else None
c1, c2, c3, c4 = st.columns(4)
c1.metric("Latest overall score", ui.fmt_score(latest["overall_score"]) if latest else "n/a",
          delta=(round(latest["overall_score"] - trend[-2]["overall_score"], 2)
                 if latest and len(trend) > 1 and latest["overall_score"] is not None
                 and trend[-2]["overall_score"] is not None else None))
c2.metric("Transaction batches", n_batches)
c3.metric("Exceptions (all)", summary["total"] if summary else 0)
c4.metric("Open exceptions", summary["by_review_status"].get("OPEN", 0) if summary else 0)

st.subheader("Quality trend")
scored = [t for t in trend if t["status"] == "scored"]
if scored:
    rows = []
    for t in scored:
        label = f"{ui.short(t['batch_id'])} ({t['source_file']})"
        for dim in ui.DIMENSIONS + ["overall"]:
            v = t.get(f"{dim}_score")
            if v is not None:
                rows.append({"batch": label, "ingested": t["ingestion_timestamp"], "dimension": dim, "score": v})
    fig = px.line(rows, x="batch", y="score", color="dimension", markers=True,
                  range_y=[max(0, int(min(r["score"] for r in rows) // 5 * 5) - 5), 100])
    fig.update_layout(xaxis_title=None, yaxis_title="Score (%)", legend_title=None, height=380)
    st.plotly_chart(fig, width="stretch")
else:
    st.info("No scored batches yet. Upload and validate a transactions file.")

if summary and summary["total"]:
    left, right = st.columns(2)
    with left:
        st.subheader("Exceptions by severity")
        sev = [{"severity": s, "count": summary["by_severity"].get(s, 0)} for s in ui.SEVERITIES]
        fig = px.bar(sev, x="severity", y="count", color="severity", color_discrete_map=ui.SEVERITY_COLOURS)
        fig.update_layout(showlegend=False, xaxis_title=None, height=320)
        st.plotly_chart(fig, width="stretch")
    with right:
        st.subheader("Review status")
        stat = [{"status": s, "count": summary["by_review_status"].get(s, 0)} for s in ui.STATUSES]
        fig = px.bar(stat, x="status", y="count", color="status", color_discrete_map=ui.STATUS_COLOURS)
        fig.update_layout(showlegend=False, xaxis_title=None, height=320)
        st.plotly_chart(fig, width="stretch")
    st.caption("Exception counts cover all datasets and batches.")

st.subheader("Recent batches")
if batches:
    df = ui.to_frame(batches[:10], ["source_file", "dataset_name", "total_records", "processing_status",
                                    "ingestion_timestamp", "uploaded_by", "batch_id"])
    st.dataframe(df, width="stretch", hide_index=True)
