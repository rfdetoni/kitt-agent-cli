"""Base protocol and common request structure for provider runtime adapters."""
from __future__ import annotations

import socket
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Protocol

from kitt.llm.domain import (
    ModelDescriptor,
    ModelDiscoveryResult,
    ProviderDiscoveryStatus,
    ProviderHealth,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderProtocolError,
    ProviderModelNotFoundError,
)


@dataclass
class LLMRequest:
    model: str
    messages: List[Dict[str, str]]
    system_prompt: Optional[str] = None
    response_format: Optional[str] = None
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
        """Streams text chunks from the provider endpoint."""
        ...

    def list_models(self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 5.0) -> ModelDiscoveryResult:
        """Discovers available runtime models from the provider endpoint."""
        ...

    def health(self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 5.0) -> ProviderHealth:
        """Probes endpoint availability and latency."""
        ...


def handle_http_error(e: urllib.error.HTTPError, url: str) -> None:
    """Translates urllib HTTPError into strongly-typed Provider exceptions."""
    body = ""
    try:
        body = e.read().decode("utf-8", errors="replace")
    except Exception:
        pass

    msg = f"HTTP {e.code}: {e.reason}" + (f" - {body}" if body else "")

    if e.code in (401, 403):
        raise ProviderAuthError(f"Authentication failed for {url} ({msg})")
    elif e.code == 404:
        raise ProviderModelNotFoundError(f"Model or endpoint not found at {url} ({msg})")
    elif e.code == 429:
        retry_after = None
        if "Retry-After" in e.headers:
            try:
                retry_after = float(e.headers["Retry-After"])
            except Exception:
                pass
        raise ProviderRateLimitError(f"Rate limited by {url} ({msg})", retry_after=retry_after)
    elif e.code >= 500:
        raise ProviderConnectionError(f"Server error from {url} ({msg})")
    else:
        raise ProviderProtocolError(f"Protocol error from {url} ({msg})")
