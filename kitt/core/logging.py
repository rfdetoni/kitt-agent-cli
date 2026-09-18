"""Structured diagnostic logging for K.I.T.T."""
from __future__ import annotations

import json
import logging
import logging.handlers
import re
import time
from pathlib import Path
from typing import Any, Dict


TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")

_SENSITIVE_KEY = re.compile(
    r"(authorization|cookie|token|secret|password|passwd|api[-_]?key|credential|csrf|xsrf)",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _sanitize_url(raw: str) -> str:
    try:
        from urllib.parse import urlsplit, urlunsplit

        parsed = urlsplit(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw
        suffix = "?[redacted]" if parsed.query or parsed.fragment else ""
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")) + suffix
    except Exception:
        return raw


def sanitize_message(value: str) -> str:
    return _URL.sub(lambda match: _sanitize_url(match.group(0)), str(value))


def sanitize_value(value: Any, key: str = "") -> Any:
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return sanitize_message(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, BaseException):
        return {
            "type": type(value).__name__,
            "message": sanitize_message(str(value)),
        }
    if isinstance(value, dict):
        return {
            str(child_key): sanitize_value(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize_value(item, key) for item in value]
    try:
        return sanitize_message(str(value))
    except Exception:
        return f"<{type(value).__name__}>"


class StructuredFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        data: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "module": record.name,
            "msg": sanitize_message(record.getMessage()),
        }
        if record.exc_info:
            data["exc_info"] = sanitize_message(self.formatException(record.exc_info))
        extra = getattr(record, "extra_data", None)
        if isinstance(extra, dict):
            data["extra_data"] = sanitize_value(extra)
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_logging(level: int = 0, path: str | Path | None = None) -> Path | None:
    """Configure K.I.T.T. diagnostics.

    Level 0 keeps the default quiet behavior.
    Level 1 records DEBUG lifecycle events.
    Level 2 records TRACE payloads, including sanitized model/tool request and response data.
    """
    numeric = int(level)
    if numeric not in {0, 1, 2}:
        raise ValueError("log level must be 0, 1 or 2")

    logger = logging.getLogger("kitt")
    for handler in list(logger.handlers):
        if getattr(handler, "_kitt_diagnostic_handler", False):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    if numeric == 0:
        logger.setLevel(logging.NOTSET)
        logger.propagate = True
        return None

    if path is None:
        raise ValueError("a log file path is required when log level is enabled")

    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        target,
        maxBytes=20_000_000 if numeric >= 2 else 5_000_000,
        backupCount=5 if numeric >= 2 else 3,
        encoding="utf-8",
    )
    handler._kitt_diagnostic_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(StructuredFormatter())

    logger.setLevel(TRACE_LEVEL if numeric >= 2 else logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False
    return target


def debug_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(event, extra={"extra_data": {"event": event, **fields}})


def trace_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    if logger.isEnabledFor(TRACE_LEVEL):
        logger.log(TRACE_LEVEL, event, extra={"extra_data": {"event": event, **fields}})


def get_logger(name: str) -> logging.Logger:
    """Returns a module logger configured for structured diagnostics."""
    return logging.getLogger(name)
