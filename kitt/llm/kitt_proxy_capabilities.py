"""Dynamic capability discovery for kitt-reverse-proxy endpoints."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from kitt.llm.http_security import secure_urlopen


@dataclass(frozen=True)
class KittProxyCapabilities:
    discovered: bool = False
    session_header: Optional[str] = None
    request_id_header: Optional[str] = None
    reasoning_header: Optional[str] = None
    reasoning_supported: Optional[bool] = None
    reasoning_range: Tuple[int, int] = (0, 100)
    session_management: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def provider(self) -> Optional[str]:
        value = self.session_management.get("provider")
        return str(value).strip().lower() if isinstance(value, str) and value.strip() else None

    @property
    def accepts_named_sessions(self) -> Optional[bool]:
        value = self.session_management.get("accepts_named_sessions")
        return value if isinstance(value, bool) else None

    @property
    def max_sessions(self) -> Optional[int]:
        value = self.session_management.get("max")
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def idle_timeout_ms(self) -> Optional[int]:
        value = self.session_management.get("idle_timeout_ms")
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


_CACHE_TTL_SECONDS = 30.0
_CACHE: Dict[str, tuple[float, KittProxyCapabilities]] = {}
_CACHE_LOCK = threading.RLock()


def _capabilities_url(base_url: str) -> str:
    base = str(base_url or "http://127.0.0.1:3000").strip().rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if base.endswith("/v1"):
        return f"{base}/capabilities"
    return f"{base}/v1/capabilities"


def _safe_header(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 128:
        return None
    if not all(ch.isalnum() or ch in "-_." for ch in value):
        return None
    return value


def _range(value: Any) -> Tuple[int, int]:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        low, high = int(value[0]), int(value[1])
        if 0 <= low <= high <= 1000:
            return low, high
    return (0, 100)


def _parse(payload: Any) -> KittProxyCapabilities:
    if not isinstance(payload, dict):
        return KittProxyCapabilities()
    contract = payload.get("kitt_agent_cli")
    if not isinstance(contract, dict):
        nested = payload.get("capabilities")
        if isinstance(nested, dict):
            contract = nested.get("kitt_agent_cli")
    if not isinstance(contract, dict):
        return KittProxyCapabilities(raw=dict(payload))

    session = contract.get("session_management")
    session_management = dict(session) if isinstance(session, dict) else {}
    reasoning = contract.get("reasoning")
    reasoning_obj = reasoning if isinstance(reasoning, dict) else {}

    reasoning_supported = reasoning_obj.get("supported")
    if not isinstance(reasoning_supported, bool):
        explicit = contract.get("reasoning_supported")
        reasoning_supported = explicit if isinstance(explicit, bool) else None
    if reasoning_supported is None:
        provider = session_management.get("provider")
        if isinstance(provider, str) and provider.strip():
            # The reverse proxy currently exposes native UI reasoning only for ChatGPT.
            reasoning_supported = provider.strip().lower() == "chatgpt"

    reasoning_header = _safe_header(reasoning_obj.get("header"))
    if reasoning_header is None:
        reasoning_header = _safe_header(contract.get("reasoning_header"))

    session_header = _safe_header(session_management.get("header"))
    if session_header is None:
        session_header = _safe_header(contract.get("session_header"))

    reasoning_range = _range(reasoning_obj.get("range"))
    if reasoning_range == (0, 100):
        reasoning_range = _range(contract.get("reasoning_range"))

    return KittProxyCapabilities(
        discovered=True,
        session_header=session_header,
        request_id_header=_safe_header(contract.get("request_id_header")),
        reasoning_header=reasoning_header,
        reasoning_supported=reasoning_supported,
        reasoning_range=reasoning_range,
        session_management=session_management,
        raw=dict(payload),
    )


def discover_kitt_proxy_capabilities(
    base_url: str,
    *,
    api_key: Optional[str] = None,
    timeout: float = 1.0,
    force: bool = False,
) -> KittProxyCapabilities:
    """Fetch and cache the proxy capability contract.

    Discovery is advisory: connection/protocol failures return an undiscovered
    snapshot so callers can retain backwards-compatible behavior without
    making the proxy a new startup dependency.
    """
    url = _capabilities_url(base_url)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(url)
        if not force and cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    headers = {
        "Accept": "application/json",
        "User-Agent": "Kitt-Agent-CLI/capability-discovery",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers, method="GET")

    snapshot = KittProxyCapabilities()
    try:
        with secure_urlopen(
            request,
            timeout=max(0.1, min(float(timeout), 5.0)),
            max_body_bytes=256 * 1024,
            max_line_bytes=256 * 1024,
        ) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
            snapshot = _parse(payload)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        urllib.error.HTTPError,
        urllib.error.URLError,
    ):
        snapshot = KittProxyCapabilities()

    with _CACHE_LOCK:
        _CACHE[url] = (time.monotonic(), snapshot)
    return snapshot


def clear_kitt_proxy_capability_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
