"""Observabilidad (9.3): logs JSON una línea por evento, sin secretos."""

from __future__ import annotations

import json
import logging
from typing import Any

_FIELDS = ("run_id", "stage", "event", "duration_ms", "provider", "model", "usage", "retry_count")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for f in _FIELDS:
            v = getattr(record, f, None)
            if v is not None:
                payload[f] = v
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("bore")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
