"""Upload safety checks. Pure functions, no I/O beyond hashing bytes."""
import hashlib
import re

from backend.errors import DataGuardError

# Browsers and OSes label CSV files inconsistently, so a handful of types is accepted.
# Anything clearly not text (image/*, application/pdf, zip ...) is refused.
ALLOWED_CONTENT_TYPES = {
    "", "text/csv", "application/csv", "text/plain", "application/vnd.ms-excel",
    "application/octet-stream", "text/x-csv",
}
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._ ()\-]")


def sanitize_filename(name: str | None) -> str:
    """Return a display-safe file name: no path components, no control characters."""
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = _SAFE_CHARS.sub("_", base).strip(" .")
    if not base:
        return "upload.csv"
    if len(base) > 200:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 200 - len(ext) - 1] + dot + ext) if dot else base[:200]
    return base


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_upload_metadata(filename: str | None, content_type: str | None) -> None:
    if not filename or not filename.lower().endswith(".csv"):
        raise DataGuardError("unsupported_type", "Only .csv files are supported.")
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_CONTENT_TYPES:
        raise DataGuardError("unsupported_type", f"Content type '{ctype}' is not accepted for CSV upload.")


def check_size(size: int, max_bytes: int) -> None:
    if size > max_bytes:
        raise DataGuardError("too_large", f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")


def decode_csv_bytes(data: bytes) -> str:
    """Strict UTF-8 decode (a BOM is tolerated). Rejects empty and binary content."""
    if not data.strip():
        raise DataGuardError("empty", "The file is empty.")
    if b"\x00" in data:
        raise DataGuardError("unsupported_type", "The file looks binary, not like a CSV text file.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DataGuardError("malformed", "The file is not valid UTF-8 text. Re-export it as UTF-8 CSV.")
