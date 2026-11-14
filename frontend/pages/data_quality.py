import plotly.express as px
import streamlit as st

import api_client as api
from components import ui

st.title("Data Quality")
if not ui.require_api():
    st.stop()

batch = ui.pick_batch("dq_batch", statuses=("COMPLETED",))
if not batch:
    st.stop()
bid = batch["batch_id"]

q = ui.call(api.get, f"/batches/{bid}/quality")
vs = ui.call(api.get, f"/batches/{bid}/validation-summary")
if not q or not vs:
    st.stop()

if q["status"] == "no_data":
    st.warning("This batch has no records, so no score was calculated.")
    st.stop()

cols = st.columns(5)
cols[0].metric("Overall", ui.fmt_score(q["overall_score"]))
for col, dim in zip(cols[1:], ui.DIMENSIONS):
    col.metric(dim.capitalize(), ui.fmt_score(q[f"{dim}_score"]))
st.caption(f"{q['valid_records']:,} of {q['total_records']:,} records have no failures "
           f"({q['invalid_records']:,} invalid). Calculated {str(q['calculated_at'])[:19].replace('T', ' ')} UTC. "
           f"Dimensions assessed: {', '.join(q['dimensions_assessed'])}.")

dims = [{"dimension": d, "score": q[f"{d}_score"]} for d in ui.DIMENSIONS if q[f"{d}_score"] is not None]
left, right = st.columns(2)
with left:
    st.subheader("Score by dimension")
    fig = px.bar(dims, x="dimension", y="score", range_y=[0, 100], text="score")
    fig.update_traces(textposition="outside")
    fig.update_layout(xaxis_title=None, yaxis_title="Score (%)", height=340)
    st.plotly_chart(fig, width="stretch")
with right:
    st.subheader("Failures by severity")
    sev = [{"severity": s, "failures": vs["failures_by_severity"].get(s, 0)} for s in ui.SEVERITIES]
    fig = px.bar(sev, x="severity", y="failures", color="severity", color_discrete_map=ui.SEVERITY_COLOURS)
    fig.update_layout(showlegend=False, xaxis_title=None, height=340)
    st.plotly_chart(fig, width="stretch")

st.subheader("Failures by rule")
rules = [r for r in vs["failures_by_rule"] if r["failures"] > 0]
if rules:
    for r in rules:
        r["rule"] = f"{r['rule_code']} {r['rule_name']}"
    fig = px.bar(rules, y="rule", x="failures", color="severity", orientation="h",
                 color_discrete_map=ui.SEVERITY_COLOURS)
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, yaxis_title=None, height=max(260, 34 * len(rules)))
    st.plotly_chart(fig, width="stretch")
else:
    st.success("No rule failures in this batch.")

with st.expander("Rules that ran for this batch"):
    ran = vs.get("rules_executed") or []
    if ran:
        st.dataframe(ui.to_frame(ran), width="stretch", hide_index=True)
    st.caption(f"Validation took {vs['duration_ms']} ms." if vs.get("duration_ms") is not None else "")

with st.expander("Score history for this batch"):
    hist = ui.call(api.get, f"/batches/{bid}/quality/history", default=[])
    st.dataframe(ui.to_frame(hist, ["metric_id", "calculated_at", "overall_score", "completeness_score",
                                    "uniqueness_score", "validity_score", "consistency_score"]),
                 width="stretch", hide_index=True)
    if st.button("Recalculate score", help="Scores the batch again and appends a history row"):
        if ui.call(api.post, f"/batches/{bid}/quality/recalculate", json={"requested_by": "dashboard"}):
            st.success("Recalculated.")
            st.rerun()

with st.expander("Validation rules (configuration)"):
    st.dataframe(ui.to_frame(ui.call(api.get, "/rules", default=[]),
                             ["rule_code", "rule_name", "rule_category", "severity", "is_active", "implemented"]),
                 width="stretch", hide_index=True)
    st.caption("Rules can be changed through the API (PATCH /rules/{id}); the dashboard shows them read-only.")
