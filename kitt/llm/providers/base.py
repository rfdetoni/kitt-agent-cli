"""Base protocol and common request structure for provider runtime adapters."""
from __future__ import annotations

import urllib.error
import json
import math
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Protocol

from kitt.llm.domain import (
    ModelDiscoveryResult,
    ProviderHealth,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderProtocolError,
    ProviderModelNotFoundError,
)
from kitt.llm.http_security import read_error_body


@dataclass
class LLMRequest:
    model: str
    messages: List[Dict[str, Any]]
    system_prompt: Optional[str] = None
    response_format: Optional[str] = None
    tool_definitions: List[Dict[str, Any]] = field(default_factory=list)
    context_envelope: Optional[Dict[str, Any]] = None
    request_metadata: Optional[Dict[str, Any]] = None
    usage_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    temperature: float = 0.0
    context_window: int = 8192
    max_output_tokens: int = 4096
    keep_alive: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    timeout_seconds: int = 300
    extra_headers: Dict[str, str] = field(default_factory=dict)


class ProviderAdapter(Protocol):
    """Protocol implemented by protocol-specific runtime adapters."""

    def stream(self, request: LLMRequest) -> Iterator[str]:
        ...

    def list_models(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 5.0,
    ) -> ModelDiscoveryResult:
        ...

    def health(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 5.0,
    ) -> ProviderHealth:
        ...


def _retry_after_seconds(headers) -> Optional[float]:
    if not headers:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        pass
    try:
        when = parsedate_to_datetime(str(raw))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _body_retry_after_seconds(body: str) -> Optional[float]:
    """Parse FreeLLMAPI reset hints without assuming a Retry-After header."""
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        payload = None
    details = payload.get("error", payload) if isinstance(payload, dict) else None
    if isinstance(details, dict):
        reset_ms = details.get("retryAtMs")
        if isinstance(reset_ms, (int, float)) and not isinstance(reset_ms, bool):
            seconds = (reset_ms / 1000.0) - time.time()
            if math.isfinite(seconds) and seconds >= 0:
                return seconds
        for name in ("retryAfter", "retry_after", "retry_after_seconds"):
            value = details.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if math.isfinite(value) and value >= 0:
                    return float(value)
    # FreeLLMAPI also reports human-readable reset ETAs on routing exhaustion.
    message = str(details.get("message") or "") if isinstance(details, dict) else ""
    match = re.search(r"soonest reset\s*~\s*(\d+)\s*([smh])\b", message, re.I)
    if match:
        return float(match.group(1)) * {"s": 1, "m": 60, "h": 3600}[match.group(2).lower()]
    return None


def handle_http_error(e: urllib.error.HTTPError, url: str, body: Optional[str] = None) -> None:
    """Translate bounded, redacted HTTP failures into typed provider errors."""
    if body is None:
        body = read_error_body(e)
    msg = f"HTTP {e.code}: {e.reason}" + (f" - {body}" if body else "")

    if e.code in (401, 403):
        raise ProviderAuthError(f"Authentication failed for provider endpoint ({msg})")
    if e.code == 404:
        raise ProviderModelNotFoundError(f"Model or endpoint not found ({msg})")
    if e.code == 429:
        header_delay = _retry_after_seconds(e.headers)
        body_delay = _body_retry_after_seconds(body)
        retry_after = max(
            (value for value in (header_delay, body_delay) if value is not None),
            default=None,
        )
        raise ProviderRateLimitError(
            f"Rate limited by provider ({msg})",
            retry_after=retry_after,
        )
    if e.code in (408, 504):
        raise ProviderTimeoutError(f"Provider request timed out ({msg})")
    if e.code >= 500:
        raise ProviderConnectionError(f"Provider server error ({msg})")
    raise ProviderProtocolError(f"Provider protocol error ({msg})")
