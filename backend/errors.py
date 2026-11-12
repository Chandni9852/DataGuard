"""Domain errors raised by services. The API layer maps `kind` to an HTTP status code,
so services stay independent of FastAPI."""

# kind -> HTTP status
STATUS_BY_KIND = {
    "bad_request": 400,
    "not_found": 404,
    "duplicate_file": 409,
    "too_large": 413,
    "unsupported_type": 415,
    "malformed": 422,
    "empty": 422,
    "missing_columns": 422,
    "conflict": 409,
    "reference_missing": 409,
    "rule_config": 500,
}


class DataGuardError(Exception):
    def __init__(self, kind: str, message: str, **details):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.details = details

    @property
    def status_code(self) -> int:
        return STATUS_BY_KIND.get(self.kind, 400)
