"""DataGuard dashboard entrypoint:  streamlit run frontend/app.py"""
import streamlit as st

st.set_page_config(page_title="DataGuard", page_icon="🛡️", layout="wide")

pages = [
    st.Page("pages/overview.py", title="Overview", icon="📊", default=True),
    st.Page("pages/data_quality.py", title="Data Quality", icon="✅"),
    st.Page("pages/exceptions.py", title="Exception Management", icon="⚠️"),
    st.Page("pages/audit_lineage.py", title="Audit & Lineage", icon="🧾"),
    st.Page("pages/upload_reports.py", title="Upload & Reports", icon="📤"),
]
nav = st.navigation(pages)

import api_client as api  # noqa: E402  (after set_page_config)

st.sidebar.caption(f"API: {api.API_URL}")
st.sidebar.caption("Synthetic data only. No authentication: run locally.")
nav.run()
