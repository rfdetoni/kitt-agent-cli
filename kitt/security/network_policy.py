"""SSRF protection and network policy for HTTP/HTTPS requests."""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from typing import Tuple

BLOCKED_METADATA_DOMAINS = frozenset({
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data",
})
BLOCKED_SINGLE_IPS = frozenset({"169.254.169.254", "0.0.0.0", "255.255.255.255"})


class NetworkPolicy:
    """Fail-closed URL validation including DNS resolution."""

    @staticmethod
    def _classify(address: str, *, allow_loopback: bool, allow_private: bool) -> str | None:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return "invalid resolved address"
        if str(ip) in BLOCKED_SINGLE_IPS:
            return "metadata/blocked IP"
        if ip.is_link_local:
            return "link-local address"
        if ip.is_multicast:
            return "multicast address"
        if ip.is_unspecified:
            return "unspecified address"
        if ip.is_loopback and not allow_loopback:
            return "loopback address"
        if ip.is_private and not (allow_private or (allow_loopback and ip.is_loopback)):
            return "private address"
        if getattr(ip, "is_reserved", False) and not ip.is_loopback:
            return "reserved address"
        return None

    @classmethod
    def validate_url(
        cls,
        url: str,
        *,
        allow_private: bool = False,
        allow_loopback: bool = True,
        resolve_dns: bool = True,
    ) -> Tuple[bool, str]:
        if not url:
            return False, "URL cannot be empty"
        try:
            parsed = urllib.parse.urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            return False, f"Invalid URL: {exc}"

        scheme = (parsed.scheme or "").lower()
        if scheme not in {"http", "https"}:
            return False, f"Unsupported URL scheme '{scheme or '<missing>'}'"
        if parsed.username is not None or parsed.password is not None:
            return False, "Credentials in URL authority are forbidden"

        host = (parsed.hostname or "").strip().lower().rstrip(".")
        if not host:
            return False, "Invalid URL host"
        if host in BLOCKED_METADATA_DOMAINS:
            return False, f"Access to cloud metadata domain '{host}' is forbidden"

        is_explicit_loopback_name = host == "localhost"
        literal_ip = None
        try:
            literal_ip = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            pass
        allow_explicit_loopback = allow_loopback and (
            is_explicit_loopback_name
            or (literal_ip is not None and literal_ip.is_loopback)
        )
        if literal_ip is not None:
            reason = cls._classify(
                str(literal_ip),
                allow_loopback=allow_explicit_loopback,
                allow_private=allow_private,
            )
            if reason:
                return False, f"Access to {reason} '{literal_ip}' is forbidden"

        if scheme == "http":
            if not allow_explicit_loopback:
                return False, f"Insecure HTTP forbidden for remote host '{host}'. Plain HTTP is allowed only for explicit loopback endpoints"

        effective_port = port or (443 if scheme == "https" else 80)
        addresses: set[str] = set()
        if literal_ip is not None:
            addresses.add(str(literal_ip))
        elif resolve_dns:
            try:
                infos = socket.getaddrinfo(host, effective_port, type=socket.SOCK_STREAM)
            except OSError as exc:
                return False, f"DNS resolution failed for '{host}': {exc}"
            addresses = {item[4][0] for item in infos if item and item[4]}
            if not addresses:
                return False, f"DNS resolution returned no addresses for '{host}'"

        for address in addresses:
            reason = cls._classify(
                address,
                allow_loopback=allow_explicit_loopback,
                allow_private=allow_private,
            )
            if reason:
                return False, f"Access to {reason} '{address}' is forbidden"

        return True, ""
