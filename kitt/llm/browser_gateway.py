"""Bounded browser automation client for kitt-reverse-proxy."""
from __future__ import annotations

import base64
import binascii
import json
import threading
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from kitt.llm.http_security import read_error_body, sanitize_remote_text, secure_urlopen


_ALLOWED_ACTIONS = frozenset({"open", "inspect", "click", "type", "screenshot", "close"})
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 6 * 1024 * 1024
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MAX_ORIGIN_SCOPE_ENTRIES = 16
_LOOPBACK_SCOPE = "loopback"


class BrowserGatewayError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserImage:
    mime_type: str
    data: bytes


def _proxy_root(base_url: str) -> str:
    base = str(base_url or "http://127.0.0.1:3000").strip().rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if base.endswith("/v1"):
        base = base[:-3]
    return base.rstrip("/")


def _normalize_origin_scope(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw_value in values:
        raw = str(raw_value or "").strip()
        if not raw:
            continue
        if raw == _LOOPBACK_SCOPE:
            normalized.append(_LOOPBACK_SCOPE)
            continue
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise BrowserGatewayError(f"Invalid browser origin scope: {raw!r}")
        if parsed.username or parsed.password:
            raise BrowserGatewayError("Browser origin scope must not contain URL credentials")
        host = parsed.hostname
        if ":" not in host:
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise BrowserGatewayError("Invalid browser origin hostname") from exc
        try:
            port = parsed.port
        except ValueError as exc:
            raise BrowserGatewayError("Invalid browser origin port") from exc
        display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        if (parsed.scheme == "http" and port == 80) or (
            parsed.scheme == "https" and port == 443
        ):
            port = None
        origin = f"{parsed.scheme}://{display_host}"
        if port is not None:
            origin += f":{port}"
        normalized.append(origin)
    unique = tuple(dict.fromkeys(normalized or [_LOOPBACK_SCOPE]))
    if len(unique) > _MAX_ORIGIN_SCOPE_ENTRIES:
        raise BrowserGatewayError("Browser origin scope is too large")
    return unique


def _is_loopback_host(hostname: str) -> bool:
    host = hostname.strip().lower()
    return (
        host == "localhost"
        or host.endswith(".localhost")
        or host in {"127.0.0.1", "::1"}
    )


class KittProxyBrowserGateway:
    """Session-scoped declarative browser client; never exposes arbitrary JS."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str],
        session_header: Optional[str],
        session_id: str,
        request_id_header: Optional[str],
        allowed_actions: Iterable[str],
        origin_scope_header: str,
        origin_scope: Iterable[str],
        timeout_seconds: float = 30.0,
    ):
        self.base_url = _proxy_root(base_url)
        self.api_key = api_key
        self.session_header = session_header
        self.session_id = str(session_id or "").strip()
        self.request_id_header = request_id_header
        self.origin_scope_header = str(origin_scope_header or "").strip()
        if (
            not self.origin_scope_header
            or len(self.origin_scope_header) > 128
            or not all(
                char.isalnum() or char in "-_." for char in self.origin_scope_header
            )
        ):
            raise BrowserGatewayError("Invalid browser origin scope header")
        self.origin_scope = _normalize_origin_scope(origin_scope)
        self.allowed_actions = frozenset(
            str(action).strip().lower()
            for action in allowed_actions
            if str(action).strip().lower() in _ALLOWED_ACTIONS
        )
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        self._image_lock = threading.RLock()
        self._pending_images: list[BrowserImage] = []

    def _url_allowed(self, value: str) -> bool:
        try:
            parsed = urlsplit(str(value or "").strip())
        except ValueError:
            return False
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        if _LOOPBACK_SCOPE in self.origin_scope and _is_loopback_host(parsed.hostname):
            return True
        host = parsed.hostname
        if ":" not in host:
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError:
                return False
        try:
            port = parsed.port
        except ValueError:
            return False
        display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        if (parsed.scheme == "http" and port == 80) or (
            parsed.scheme == "https" and port == 443
        ):
            port = None
        origin = f"{parsed.scheme}://{display_host}"
        if port is not None:
            origin += f":{port}"
        return origin in self.origin_scope

    def execute(self, action: str, arguments: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        normalized = str(action or "").strip().lower()
        if normalized not in self.allowed_actions:
            raise BrowserGatewayError(f"Browser action is not advertised by the proxy: {normalized!r}")
        args = arguments or {}
        if not isinstance(args, dict):
            raise BrowserGatewayError("Browser arguments must be an object")
        if normalized == "open":
            target = args.get("url")
            if not isinstance(target, str) or not self._url_allowed(target):
                raise BrowserGatewayError(
                    "Browser navigation target is outside the user-authorized origin scope"
                )
        body = json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > _MAX_REQUEST_BYTES:
            raise BrowserGatewayError("Browser request exceeds the bounded request size")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Kitt-Agent-CLI/browser-gateway",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.session_header and self.session_id:
            headers[self.session_header] = self.session_id
        if self.request_id_header:
            headers[self.request_id_header] = uuid.uuid4().hex
        scope_json = json.dumps(
            list(self.origin_scope),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers[self.origin_scope_header] = (
            base64.urlsafe_b64encode(scope_json).rstrip(b"=").decode("ascii")
        )

        request = urllib.request.Request(
            f"{self.base_url}/v1/kitt/browser/{normalized}",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with secure_urlopen(
                request,
                timeout=self.timeout_seconds,
                max_body_bytes=_MAX_RESPONSE_BYTES,
                max_line_bytes=_MAX_RESPONSE_BYTES,
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = read_error_body(exc) or f"HTTP {exc.code}"
            raise BrowserGatewayError(
                f"Reverse-proxy browser request failed: {sanitize_remote_text(detail)}"
            ) from exc
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise BrowserGatewayError(
                f"Reverse-proxy browser request failed: {sanitize_remote_text(str(exc))}"
            ) from exc

        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrowserGatewayError("Reverse-proxy browser response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise BrowserGatewayError("Reverse-proxy browser response must be an object")
        resulting_url = payload.get("url")
        if isinstance(resulting_url, str) and resulting_url and not self._url_allowed(resulting_url):
            raise BrowserGatewayError(
                "Reverse-proxy browser response escaped the user-authorized origin scope"
            )

        if normalized == "screenshot":
            encoded = payload.pop("image_base64", None)
            if not isinstance(encoded, str) or not encoded:
                raise BrowserGatewayError("Screenshot response did not include image data")
            try:
                image = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise BrowserGatewayError("Screenshot response contained invalid base64") from exc
            if len(image) > _MAX_IMAGE_BYTES:
                raise BrowserGatewayError("Screenshot exceeds the bounded image size")
            fmt = str(payload.get("format") or "jpeg").strip().lower()
            mime = "image/png" if fmt == "png" else "image/jpeg"
            with self._image_lock:
                self._pending_images.append(BrowserImage(mime, image))
            payload["image_attached"] = True
            payload["image_mime_type"] = mime
        return payload

    def drain_images(self) -> tuple[BrowserImage, ...]:
        with self._image_lock:
            images = tuple(self._pending_images)
            self._pending_images.clear()
            return images
