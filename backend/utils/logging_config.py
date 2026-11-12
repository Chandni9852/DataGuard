"""Central logging setup. Never log row contents or credentials."""
import logging
import re

_URL_PASSWORD = re.compile(r"(://[^:/\s]+:)[^@\s]+(@)")


class RedactingFilter(logging.Filter):
    """Masks passwords embedded in connection URLs if one ever reaches a log message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _URL_PASSWORD.sub(r"\1***\2", str(record.msg))
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
