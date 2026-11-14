"""Thin HTTP client for the DataGuard API. The dashboard talks to the API only; it never touches the database."""
import os

import requests

API_URL = os.environ.get("DATAGUARD_API_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")
TIMEOUT = 60


class ApiError(Exception):
    """The API answered with an error status, or could not be reached."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


def _request(method: str, path: str, **kw):
    try:
        r = requests.request(method, f"{API_URL}{path}", timeout=kw.pop("timeout", TIMEOUT), **kw)
    except requests.RequestException as e:
        raise ApiError(f"Cannot reach the DataGuard API at {API_URL}. Is it running? ({type(e).__name__})") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        if isinstance(detail, list):                      # FastAPI validation errors
            detail = "; ".join(str(d.get("msg", d)) for d in detail)
        raise ApiError(str(detail), r.status_code)
    return r


def _clean(params: dict | None) -> dict:
    return {k: v for k, v in (params or {}).items() if v not in (None, "", "All")}


def get(path: str, **params):
    return _request("GET", path, params=_clean(params)).json()


def get_with_total(path: str, **params) -> tuple[list, int]:
    r = _request("GET", path, params=_clean(params))
    data = r.json()
    return data, int(r.headers.get("X-Total-Count", len(data)))


def post(path: str, json: dict | None = None, **kw):
    return _request("POST", path, json=json, **kw).json()


def upload(path: str, filename: str, content: bytes, **fields):
    files = {"file": (filename, content, "text/csv")}
    return _request("POST", path, files=files, data={k: str(v).lower() if isinstance(v, bool) else v
                                                     for k, v in fields.items() if v is not None}).json()


def download_csv(**params) -> bytes:
    return _request("GET", "/reports/exceptions.csv", params=_clean(params), timeout=300).content


def health() -> dict:
    return get("/health")
