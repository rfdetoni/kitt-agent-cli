"""Classify processing locality, independently of gateway bind address."""
from __future__ import annotations
import ipaddress
from urllib.parse import urlparse

LOCAL_BACKENDS = frozenset({"ollama", "lmstudio", "local", "localai", "vllm"})

def profile_processing_is_local(profile) -> bool:
    backend = str(getattr(profile, "backend", "") or "").strip().lower()
    protocol = str(getattr(profile, "protocol", "") or "").strip().lower()
    if "kitt" in protocol and "proxy" in protocol:
        return False
    if backend not in LOCAL_BACKENDS:
        return False
    endpoint = str(getattr(profile, "base_url", "") or "").strip()
    if not endpoint:
        return True
    try:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.hostname.lower() == "localhost":
            return True
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False
