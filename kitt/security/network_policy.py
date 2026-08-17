"""SSRF protection and network policy for HTTP/HTTPS requests."""
from __future__ import annotations

import ipaddress
import urllib.parse
from typing import Tuple

# IPs e domínios de metadata cloud conhecidos
BLOCKED_METADATA_DOMAINS = frozenset({
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data",
})

BLOCKED_SINGLE_IPS = frozenset({"169.254.169.254", "0.0.0.0", "255.255.255.255"})


class NetworkPolicy:
    """Enforces HTTPS for remote requests and blocks SSRF / metadata IP endpoints."""

    @staticmethod
    def validate_url(url: str) -> Tuple[bool, str]:
        if not url:
            return False, "URL cannot be empty"

        parsed = urllib.parse.urlparse(url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()

        if not host:
            return False, "Invalid URL host"

        is_loopback = host in ("127.0.0.1", "localhost", "::1")

        # Checar domínios de metadata por nome
        if host in BLOCKED_METADATA_DOMAINS:
            return False, f"Access to cloud metadata domain '{host}' is forbidden (SSRF protection)."

        # Checar IPs singulares bloqueados
        if host in BLOCKED_SINGLE_IPS:
            return False, f"Access to metadata/blocked IP '{host}' is forbidden (SSRF protection)."

        try:
            ip = ipaddress.ip_address(host)
            if ip.is_loopback and not is_loopback:
                return False, f"Access to loopback IP '{host}' is forbidden."
            if ip.is_link_local:
                return False, f"Access to link-local IP '{host}' is forbidden (SSRF — includes cloud metadata)."
            if ip.is_multicast:
                return False, f"Access to multicast IP '{host}' is forbidden."
            if ip.is_unspecified:
                return False, f"Access to unspecified IP '{host}' is forbidden."
            if ip.is_private and not is_loopback:
                return False, f"Access to private IP '{host}' is forbidden (SSRF protection)."
        except ValueError:
            pass  # host é nome de domínio — OK

        if scheme == "http" and not is_loopback:
            return False, f"Insecure HTTP forbidden for remote host '{host}'. Use HTTPS."

        return True, ""
