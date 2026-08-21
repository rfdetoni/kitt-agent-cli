from __future__ import annotations

import ipaddress
import threading
import time
from collections import deque
from urllib.parse import urlsplit


_ALLOWED_LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain"}


def is_private_client(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def _host_only(host_header: str) -> str:
    value = (host_header or "").strip()
    if not value:
        return ""
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else ""
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def is_allowed_host(host_header: str) -> bool:
    host = _host_only(host_header).rstrip(".").lower()
    if host in _ALLOWED_LOCAL_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def origin_matches_host(origin: str | None, host_header: str) -> bool:
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    return parsed.netloc.lower() == (host_header or "").strip().lower()


class SlidingWindowLimiter:
    """Small bounded per-key rate limiter for pairing and mutations."""

    def __init__(self, limit: int, window_seconds: float, max_keys: int = 512) -> None:
        self.limit = max(1, int(limit))
        self.window = max(1.0, float(window_seconds))
        self.max_keys = max(8, int(max_keys))
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            q = self._events.setdefault(str(key), deque())
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._events) > self.max_keys:
                for old_key in list(self._events)[: len(self._events) - self.max_keys]:
                    if old_key != key:
                        self._events.pop(old_key, None)
            return True
