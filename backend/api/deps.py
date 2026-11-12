"""Shared API helpers."""
from fastapi import UploadFile

from backend.config import get_settings
from backend.errors import DataGuardError


def read_upload(file: UploadFile) -> bytes:
    """Read an upload, refusing anything over the size limit without loading it all."""
    limit = get_settings().max_upload_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise DataGuardError("too_large", f"File exceeds the {limit // (1024 * 1024)} MB upload limit.")
    return data
