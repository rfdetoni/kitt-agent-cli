"""Structured JSON logging formatter and setup for K.I.T.T."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict


class StructuredFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        data: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc_info"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_data", None)
        if isinstance(extra, dict):
            data["extra_data"] = extra
        return json.dumps(data, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    """Returns a module logger configured for structured diagnostics."""
    return logging.getLogger(name)
