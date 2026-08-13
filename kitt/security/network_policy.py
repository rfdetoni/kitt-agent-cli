"""SSRF protection and network policy for HTTP/HTTPS requests."""

from __future__ import annotations

import ipaddress
import urllib.parse
from typing import Tuple

BLOCKED_IPS = {"169.254.169.254", "0.0.0.0", "255.255.255.255"}


class NetworkPolicy:
    """Enforces HTTPS for remote requests and blocks SSRF / metadata IP endpoints."""

    @staticmethod
    def validate_url(url: str) -> Tuple[bool, str]:
        """Validate URL against HTTPS requirement and SSRF rules.

        Returns (is_allowed, error_message).
        """
        if not url:
            return False, "URL cannot be empty"

        parsed = urllib.parse.urlparse(url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()

        if not host:
            return False, "Invalid URL host"

        is_loopback = host in ("127.0.0.1", "localhost", "::1")

        # SSRF / metadata IP check (checked before scheme validation)
        if host in BLOCKED_IPS:
            return False, f"Access to metadata/blocked IP '{host}' is forbidden (SSRF protection)."

        if scheme == "http" and not is_loopback:
            return False, f"Insecure HTTP forbidden for remote host '{host}'. Use HTTPS."

        try:
            ip = ipaddress.ip_address(host)
            if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                return False, f"Access to link-local/multicast IP '{host}' is forbidden."
        except ValueError:
            pass  # Host is domain name

        return True, ""
