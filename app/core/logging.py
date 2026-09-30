"""JSON-логи с request_id и маскированием email (NFR-10, NFR-13)."""

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def mask_email(text: str) -> str:
    """anna.petrova@mail.ru -> a***@mail.ru"""
    return _EMAIL_RE.sub(r"\1***\2", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_email(record.getMessage()),
        }
        request_id = request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        for key in ("order_id", "payment_id", "event_id", "command_id"):
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Библиотеки брокеров слишком разговорчивы на INFO
    logging.getLogger("pika").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.WARNING)
