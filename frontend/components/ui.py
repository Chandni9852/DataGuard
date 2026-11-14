"""Shared Streamlit helpers: error handling, colours, formatting."""
import functools

import pandas as pd
import streamlit as st

import api_client as api

SEVERITY_COLOURS = {"CRITICAL": "#B42318", "HIGH": "#E8590C", "MEDIUM": "#E6B800", "LOW": "#6C8EAD"}
STATUS_COLOURS = {"OPEN": "#B42318", "IN_REVIEW": "#E6B800", "REVIEWED": "#2F9E44", "WAIVED": "#868E96"}
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
STATUSES = ["OPEN", "IN_REVIEW", "REVIEWED", "WAIVED"]
CATEGORIES = ["COMPLETENESS", "UNIQUENESS", "VALIDITY", "CONSISTENCY", "REFERENTIAL_INTEGRITY"]
DIMENSIONS = ["completeness", "uniqueness", "validity", "consistency"]
PAGE_SIZE = 25


def call(fn, *args, default=None, **kw):
    """Run an API call; show a readable error instead of a traceback."""
    try:
        return fn(*args, **kw)
    except api.ApiError as e:
        st.error(e.message)
        return default


def short(batch_id: str) -> str:
    return str(batch_id)[:8]


def batch_label(b: dict) -> str:
    ts = str(b["ingestion_timestamp"])[:16].replace("T", " ")
    return f"{b['source_file']} · {short(b['batch_id'])} · {ts} · {b['processing_status']}"


def pick_batch(key: str, statuses: tuple[str, ...] | None = None, dataset_id: int | None = 1):
    """Batch selector (newest first). Returns the batch dict or None."""
    batches = call(api.get, "/batches", default=[], dataset_id=dataset_id, limit=200)
    if statuses:
        batches = [b for b in batches if b["processing_status"] in statuses]
    if not batches:
        st.info("No batches yet. Upload a file on the **Upload & Reports** page.")
        return None
    return st.selectbox("Batch", batches, format_func=batch_label, key=key)


def score_colour(v: float | None) -> str:
    if v is None:
        return "gray"
    return "green" if v >= 95 else "orange" if v >= 80 else "red"


def fmt_score(v) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def to_frame(rows: list[dict], columns: list[str] | None = None) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if columns and not df.empty:
        df = df[[c for c in columns if c in df.columns]]
    return df


def require_api() -> bool:
    try:
        api.health()
        return True
    except api.ApiError as e:
        st.error(e.message)
        return False
