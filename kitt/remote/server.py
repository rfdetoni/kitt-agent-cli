from __future__ import annotations

import json
import mimetypes
import os
import ipaddress
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from kitt.daemon.protocol import DaemonEvent
from kitt.remote.auth import PairingAuth
from kitt.remote.gateway import DaemonGateway
from kitt.remote.security import (
    SlidingWindowLimiter,
    is_allowed_host,
    is_private_client,
    origin_matches_host,
)


_SESSION_COOKIE = "kitt_remote_session"
_MAX_BODY_BYTES = 256 * 1024
_MAX_PROMPT_CHARS = 128 * 1024
_SESSION_RE = re.compile(r"^/api/sessions/([A-Za-z0-9_-]{1,128})$")
_EVENTS_RE = re.compile(r"^/api/sessions/([A-Za-z0-9_-]{1,128})/events$")
_INPUT_RE = re.compile(r"^/api/sessions/([A-Za-z0-9_-]{1,128})/input$")
_CANCEL_RE = re.compile(r"^/api/turns/([A-Za-z0-9_-]{1,160})/cancel$")
_APPROVAL_RE = re.compile(r"^/api/approvals/([A-Za-z0-9_-]{1,160})/(approve|deny)$")
_ARTIFACT_RE = re.compile(r"^/api/artifacts/([A-Za-z0-9_-]{1,180})$")


@dataclass(frozen=True)
class RemoteServerConfig:
    workspace_root: str
    host: str = "127.0.0.1"
    port: int = 7337
    pairing_ttl_seconds: float = 900.0
    session_ttl_seconds: float = 43_200.0
    tls_cert: str | None = None
    tls_key: str | None = None

    def validated(self) -> "RemoteServerConfig":
        root = str(Path(self.workspace_root).expanduser().resolve())
        port = int(self.port)
        if port < 0 or port > 65535:
            raise ValueError("Remote port must be between 0 and 65535")
        host = str(self.host or "127.0.0.1").strip()
        if not host:
            raise ValueError("Remote bind host is required")
        if host not in {"localhost"}:
            try:
                ip = ipaddress.ip_address(host.split("%", 1)[0])
            except ValueError as exc:
                raise ValueError("Remote bind host must be localhost or an IP address") from exc
            if not (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified):
                raise ValueError("Remote bind host must be local/private")
        if bool(self.tls_cert) != bool(self.tls_key):
            raise ValueError("--tls-cert and --tls-key must be provided together")
        if self.tls_cert and not Path(self.tls_cert).is_file():
            raise ValueError(f"TLS certificate not found: {self.tls_cert}")
        if self.tls_key and not Path(self.tls_key).is_file():
            raise ValueError(f"TLS key not found: {self.tls_key}")
        return RemoteServerConfig(
            workspace_root=root,
            host=host,
            port=port,
            pairing_ttl_seconds=max(60.0, float(self.pairing_ttl_seconds)),
            session_ttl_seconds=max(300.0, float(self.session_ttl_seconds)),
            tls_cert=self.tls_cert,
            tls_key=self.tls_key,
        )


class _RemoteHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, address, handler, app: "RemoteServer"):
        self.app = app
        self._request_slots = threading.BoundedSemaphore(64)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        # ThreadingHTTPServer is otherwise unbounded. SSE connections are long
        # lived, so cap concurrent request threads to avoid trusted-LAN DoS or
        # accidental browser storms exhausting the host.
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class _RemoteHTTPServerV6(_RemoteHTTPServer):
    address_family = socket.AF_INET6


class RemoteRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KITT-Remote/1"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        # Bound slow headers/bodies and stalled SSE writes. Heartbeats keep a
        # healthy event stream active well inside this timeout.
        self.connection.settimeout(45.0)

    @property
    def app(self) -> "RemoteServer":
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        # Do not log cookies, request bodies, pairing codes, or query strings.
        path = urlsplit(self.path).path
        print(f"[kitt-remote] {self.client_address[0]} {self.command} {path} -> {args[1] if len(args) > 1 else ''}")

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            ),
        }
        if self.app.uses_tls:
            headers["Strict-Transport-Security"] = "max-age=31536000"
        return headers

    def _write_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        headers = self._base_headers()
        headers.update(extra_headers or {})
        headers.setdefault("Cache-Control", "no-store")
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
        headers["Connection"] = "close"
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
            self.wfile.flush()
        self.close_connection = True

    def _json(self, status: int, payload: dict, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._write_bytes(status, body, "application/json; charset=utf-8", extra_headers)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"status": "error", "error": str(message)[:2000]})

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie", "")
        if not raw or len(raw) > 8192:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return ""
        morsel = cookie.get(_SESSION_COOKIE)
        return morsel.value if morsel else ""

    def _security_gate(self) -> bool:
        client_ip = str(self.client_address[0])
        if not is_private_client(client_ip):
            self._error(HTTPStatus.FORBIDDEN, "Remote access is restricted to private/local network clients")
            return False
        if not is_allowed_host(self.headers.get("Host", "")):
            self._error(HTTPStatus.BAD_REQUEST, "Invalid Host header")
            return False
        if not origin_matches_host(self.headers.get("Origin"), self.headers.get("Host", "")):
            self._error(HTTPStatus.FORBIDDEN, "Cross-origin request blocked")
            return False
        return True

    def _require_auth(self, *, csrf: bool = False) -> str | None:
        token = self._cookie_token()
        if not self.app.auth.authenticate(token):
            self._error(HTTPStatus.UNAUTHORIZED, "Authentication required")
            return None
        if csrf:
            supplied = self.headers.get("X-KITT-CSRF", "")
            if not self.app.auth.validate_csrf(token, supplied):
                self._error(HTTPStatus.FORBIDDEN, "Invalid CSRF token")
                return None
        return token

    def _read_json(self) -> dict:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("Content-Type must be application/json")
        raw_length = self.headers.get("Content-Length", "")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 0 or length > _MAX_BODY_BYTES:
            raise ValueError(f"Request body exceeds {_MAX_BODY_BYTES} bytes")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError("Incomplete request body")
        try:
            value = json.loads(data.decode("utf-8")) if data else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid JSON body") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _serve_static(self, path: str) -> bool:
        mapping = {
            "/": "index.html",
            "/index.html": "index.html",
            "/app.js": "app.js",
            "/app.css": "app.css",
        }
        filename = mapping.get(path)
        if not filename:
            return False
        target = self.app.static_root / filename
        try:
            data = target.read_bytes()
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Web asset unavailable")
            return True
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if filename.endswith(".js"):
            content_type = "text/javascript"
        self._write_bytes(
            HTTPStatus.OK,
            data,
            f"{content_type}; charset=utf-8",
            {"Cache-Control": "no-cache"},
        )
        return True

    def do_HEAD(self) -> None:
        if not self._security_gate():
            return
        path = unquote(urlsplit(self.path).path)
        if self._serve_static(path):
            return
        if path == "/api/health":
            self._json(
                HTTPStatus.OK,
                {"status": "ok", "service": "kitt-remote", "auth_required": True},
            )
            return
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "HEAD is only supported for static assets and health")

    def do_GET(self) -> None:
        if not self._security_gate():
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if self._serve_static(path):
            return
        if path == "/api/health":
            self._json(
                HTTPStatus.OK,
                {"status": "ok", "service": "kitt-remote", "auth_required": True},
            )
            return
        token = self._require_auth()
        if not token:
            return
        try:
            if path == "/api/me":
                csrf = self.app.auth.refresh_csrf(token)
                session = self.app.auth.authenticate(token)
                if not csrf or not session:
                    self._error(HTTPStatus.UNAUTHORIZED, "Session expired")
                    return
                self._json(
                    HTTPStatus.OK,
                    {"status": "ok", "csrf": csrf, "expires_at": session.expires_at},
                )
                return
            if path == "/api/status":
                self._json(HTTPStatus.OK, self.app.gateway.status())
                return
            if path == "/api/extensions":
                self._json(HTTPStatus.OK, self.app.gateway.extensions())
                return
            if path == "/api/sessions":
                self._json(HTTPStatus.OK, self.app.gateway.list_sessions())
                return
            if path == "/api/approvals":
                session_id = (parse_qs(parsed.query).get("session_id") or [None])[0]
                self._json(HTTPStatus.OK, self.app.gateway.approvals(session_id))
                return
            if path == "/api/artifacts":
                session_id = (parse_qs(parsed.query).get("session_id") or [""])[0]
                if not session_id:
                    self._error(HTTPStatus.BAD_REQUEST, "session_id is required")
                    return
                self._json(HTTPStatus.OK, self.app.gateway.artifacts(session_id))
                return
            if path == "/api/diff":
                self._json(HTTPStatus.OK, self.app.gateway.workspace_diff())
                return
            artifact_match = _ARTIFACT_RE.match(path)
            if artifact_match:
                params = parse_qs(parsed.query)
                session_id = (params.get("session_id") or [""])[0]
                if not session_id:
                    self._error(HTTPStatus.BAD_REQUEST, "session_id is required")
                    return
                try:
                    offset = max(0, int((params.get("offset") or [0])[0]))
                except (TypeError, ValueError):
                    offset = 0
                self._json(
                    HTTPStatus.OK,
                    self.app.gateway.read_artifact(session_id, artifact_match.group(1), offset),
                )
                return
            match = _SESSION_RE.match(path)
            if match:
                params = parse_qs(parsed.query)
                before = (params.get("before") or [""])[0]
                try:
                    message_limit = max(1, min(int((params.get("limit") or [50])[0]), 100))
                except (TypeError, ValueError):
                    message_limit = 50
                include_events = str((params.get("include_events") or ["1"])[0]).lower() not in {"0", "false", "no"}
                self._json(
                    HTTPStatus.OK,
                    self.app.gateway.get_session(
                        match.group(1), before=before, message_limit=message_limit,
                        include_events=include_events, event_limit=40,
                    ),
                )
                return
            match = _EVENTS_RE.match(path)
            if match:
                self._serve_sse(match.group(1), parsed.query)
                return
        except (ConnectionError, RuntimeError, ValueError) as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc))
            return
        self._error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        if not self._security_gate():
            return
        path = unquote(urlsplit(self.path).path)
        client_ip = str(self.client_address[0])

        if path == "/api/pair":
            if not self.app.pair_limiter.allow(client_ip):
                self._error(HTTPStatus.TOO_MANY_REQUESTS, "Too many pairing attempts")
                return
            try:
                body = self._read_json()
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            result = self.app.auth.pair(str(body.get("code", "")), client_ip)
            if not result:
                self._error(HTTPStatus.UNAUTHORIZED, "Invalid or expired pairing code")
                return
            token, csrf, expires_at = result
            max_age = max(1, int(expires_at - time.time()))
            secure = "; Secure" if self.app.uses_tls else ""
            cookie = (
                f"{_SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; "
                f"Max-Age={max_age}{secure}"
            )
            self._json(
                HTTPStatus.OK,
                {"status": "ok", "csrf": csrf, "expires_at": expires_at},
                {"Set-Cookie": cookie},
            )
            return

        token = self._require_auth(csrf=True)
        if not token:
            return
        if not self.app.mutation_limiter.allow(client_ip):
            self._error(HTTPStatus.TOO_MANY_REQUESTS, "Too many requests")
            return
        try:
            body = self._read_json()
            if path == "/api/logout":
                self.app.auth.logout(token)
                secure = "; Secure" if self.app.uses_tls else ""
                self._json(
                    HTTPStatus.OK,
                    {"status": "ok"},
                    {"Set-Cookie": f"{_SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0{secure}"},
                )
                return
            if path == "/api/sessions":
                title = str(body.get("title") or "New Session").strip()[:160] or "New Session"
                self._json(HTTPStatus.CREATED, self.app.gateway.create_session(title))
                return
            match = _INPUT_RE.match(path)
            if match:
                text = str(body.get("text") or "")
                if not text.strip():
                    self._error(HTTPStatus.BAD_REQUEST, "Message cannot be empty")
                    return
                if len(text) > _MAX_PROMPT_CHARS:
                    self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Message is too large")
                    return
                mode = str(body.get("mode") or "auto")[:32]
                self._json(HTTPStatus.ACCEPTED, self.app.gateway.send_input(match.group(1), text, mode))
                return
            match = _CANCEL_RE.match(path)
            if match:
                session_id = str(body.get("session_id") or "")
                if not session_id:
                    self._error(HTTPStatus.BAD_REQUEST, "session_id is required")
                    return
                self._json(HTTPStatus.OK, self.app.gateway.cancel_turn(session_id, match.group(1)))
                return
            match = _APPROVAL_RE.match(path)
            if match:
                approval_id, decision = match.groups()
                session_id = str(body.get("session_id") or "") or None
                result = (
                    self.app.gateway.approve(approval_id, session_id)
                    if decision == "approve"
                    else self.app.gateway.deny(approval_id, session_id)
                )
                self._json(HTTPStatus.ACCEPTED, result)
                return
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except (ConnectionError, RuntimeError) as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc))
            return
        self._error(HTTPStatus.NOT_FOUND, "Not found")

    def _serve_sse(self, session_id: str, query: str) -> None:
        if not self.app.sse_limiter.acquire(blocking=False):
            self._error(HTTPStatus.TOO_MANY_REQUESTS, "Server event stream capacity reached")
            return

        with self.app._sse_lock:
            count = self.app._active_sse_by_session.get(session_id, 0)
            if count >= 4:
                self.app.sse_limiter.release()
                self._error(HTTPStatus.TOO_MANY_REQUESTS, "Too many concurrent event streams for session")
                return
            self.app._active_sse_by_session[session_id] = count + 1

        sse_slot_acquired = True
        try:
            params = parse_qs(query)
            after = 0
            try:
                after = max(after, int((params.get("after") or [0])[0]))
            except (TypeError, ValueError):
                pass
            try:
                after = max(after, int(self.headers.get("Last-Event-ID", "0") or 0))
            except ValueError:
                pass

            self.send_response(HTTPStatus.OK)
            for name, value in self._base_headers().items():
                self.send_header(name, value)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            stop = threading.Event()
            write_lock = threading.Lock()

            def emit(evt: DaemonEvent) -> None:
                if stop.is_set():
                    return
                payload = json.dumps(evt.to_dict(), ensure_ascii=False, separators=(",", ":"))
                frame = (
                    f"id: {int(evt.sequence_id)}\n"
                    f"event: kitt\n"
                    f"data: {payload}\n\n"
                ).encode("utf-8")
                try:
                    with write_lock:
                        self.wfile.write(frame)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    stop.set()

            def heartbeat() -> None:
                if stop.is_set():
                    return
                try:
                    with write_lock:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    stop.set()

            try:
                self.app.gateway.stream_events(session_id, after, emit, heartbeat, stop)
            except (ConnectionError, RuntimeError, OSError):
                stop.set()
            finally:
                self.close_connection = True
        finally:
            if sse_slot_acquired:
                with self.app._sse_lock:
                    remaining = max(0, self.app._active_sse_by_session.get(session_id, 1) - 1)
                    if remaining == 0:
                        self.app._active_sse_by_session.pop(session_id, None)
                    else:
                        self.app._active_sse_by_session[session_id] = remaining
                self.app.sse_limiter.release()


class RemoteServer:
    def __init__(self, config: RemoteServerConfig, gateway: DaemonGateway | None = None) -> None:
        self.config = config.validated()
        self.gateway = gateway or DaemonGateway(self.config.workspace_root)
        self.auth = PairingAuth(
            pairing_ttl_seconds=self.config.pairing_ttl_seconds,
            session_ttl_seconds=self.config.session_ttl_seconds,
        )
        self.pair_limiter = SlidingWindowLimiter(limit=8, window_seconds=60.0)
        self.mutation_limiter = SlidingWindowLimiter(limit=120, window_seconds=60.0)
        self.sse_limiter = threading.BoundedSemaphore(48)
        self._active_sse_by_session: dict[str, int] = {}
        self._sse_lock = threading.Lock()
        self.static_root = Path(__file__).resolve().parent / "static"
        self._httpd: _RemoteHTTPServer | None = None
        self.uses_tls = bool(self.config.tls_cert and self.config.tls_key)

    @property
    def address(self) -> tuple[str, int]:
        if not self._httpd:
            return self.config.host, self.config.port
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        if self._httpd is not None:
            raise RuntimeError("Remote server is already started")
        host_for_ip = self.config.host.split("%", 1)[0]
        try:
            bind_ip = ipaddress.ip_address(host_for_ip)
        except ValueError:
            bind_ip = None
        server_class = _RemoteHTTPServerV6 if bind_ip and bind_ip.version == 6 else _RemoteHTTPServer
        httpd = server_class((self.config.host, self.config.port), RemoteRequestHandler, self)
        if self.uses_tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(self.config.tls_cert, self.config.tls_key)
            httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        self._httpd = httpd

    def serve_forever(self) -> None:
        if self._httpd is None:
            self.start()
        assert self._httpd is not None
        self._httpd.serve_forever(poll_interval=0.5)

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

    @staticmethod
    def _url_host(address: str) -> str:
        return f"[{address}]" if ":" in address and not address.startswith("[") else address

    def display_urls(self) -> list[str]:
        scheme = "https" if self.uses_tls else "http"
        host, port = self.address
        if host not in {"0.0.0.0", "::"}:
            return [f"{scheme}://{self._url_host(host)}:{port}"]
        addresses: set[str] = set()
        try:
            for info in socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_UNSPEC, socket.SOCK_STREAM
            ):
                address = info[4][0]
                if is_private_client(address) and address not in {"127.0.0.1", "::1"}:
                    addresses.add(address)
        except OSError:
            pass
        if not addresses:
            addresses.add("::1" if host == "::" else "127.0.0.1")
        return [
            f"{scheme}://{self._url_host(address)}:{port}"
            for address in sorted(addresses)
        ]
