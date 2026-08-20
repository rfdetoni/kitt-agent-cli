"""Bounded HTTP transport primitives for LLM/provider/OAuth traffic."""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Any

DEFAULT_MAX_BODY_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_LINE_BYTES = 1024 * 1024
MAX_ERROR_BYTES = 32 * 1024

_SECRET_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"\bsk-[A-Za-z0-9_-]{8,}|"
    r"(?:api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]{4,})"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class RedirectBlockedError(urllib.error.HTTPError):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def _blocked(self, req, fp, code, msg, headers):
        location = headers.get("Location", "") if headers else ""
        try:
            fp.close()
        except Exception:
            pass
        raise RedirectBlockedError(
            req.full_url,
            code,
            f"HTTP redirect blocked ({location[:256]})",
            headers,
            None,
        )

    http_error_301 = _blocked
    http_error_302 = _blocked
    http_error_303 = _blocked
    http_error_307 = _blocked
    http_error_308 = _blocked


_OPENER = urllib.request.build_opener(_NoRedirectHandler())


class BoundedHTTPResponse:
    def __init__(self, response, max_body_bytes: int, max_line_bytes: int):
        self._response = response
        self._max_body_bytes = max(1, int(max_body_bytes))
        self._max_line_bytes = max(1, int(max_line_bytes))
        self._seen = 0
        length = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
        if length:
            try:
                if int(length) > self._max_body_bytes:
                    response.close()
                    raise ValueError("HTTP response exceeds configured size limit")
            except ValueError:
                # Invalid Content-Length is not trusted; streaming accounting remains authoritative.
                if str(length).strip().isdigit():
                    raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def close(self):
        return self._response.close()

    def _account(self, data: bytes) -> bytes:
        self._seen += len(data)
        if self._seen > self._max_body_bytes:
            self.close()
            raise ValueError("HTTP response exceeds configured size limit")
        return data

    def read(self, amt: int = -1) -> bytes:
        remaining = self._max_body_bytes - self._seen
        if remaining < 0:
            raise ValueError("HTTP response exceeds configured size limit")
        if amt is None or amt < 0:
            data = self._response.read(remaining + 1)
            return self._account(data)
        data = self._response.read(min(int(amt), remaining + 1))
        return self._account(data)

    def readline(self, limit: int = -1) -> bytes:
        remaining = self._max_body_bytes - self._seen
        line_limit = min(self._max_line_bytes + 1, remaining + 1)
        if limit is not None and limit >= 0:
            line_limit = min(line_limit, int(limit))
        data = self._response.readline(line_limit)
        if len(data) > self._max_line_bytes:
            self.close()
            raise ValueError("HTTP response line exceeds configured size limit")
        return self._account(data)

    def __iter__(self):
        while True:
            line = self.readline()
            if not line:
                return
            yield line


def secure_urlopen(
    request,
    timeout: float = 10.0,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
):
    response = _OPENER.open(request, timeout=timeout)
    return BoundedHTTPResponse(response, max_body_bytes, max_line_bytes)


def read_error_body(error: urllib.error.HTTPError, max_bytes: int = MAX_ERROR_BYTES) -> str:
    try:
        raw = error.read(max_bytes + 1)
    except Exception:
        return ""
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    return sanitize_remote_text(raw.decode("utf-8", "replace"))


def sanitize_remote_text(text: str, max_chars: int = 4096) -> str:
    clean = _CONTROL_RE.sub(" ", str(text or ""))
    clean = _SECRET_RE.sub("[REDACTED]", clean)
    clean = " ".join(clean.split())
    if len(clean) > max_chars:
        clean = clean[:max_chars] + "…"
    return clean
