"""Authenticated client for the dedicated kitt-memory service."""
from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import sys
import time
import shutil
from pathlib import Path
from typing import Any

from kitt_protocol import (
    AuthenticatedFrame,
    Envelope,
    MAX_FRAME_BYTES,
    MEMORY_FORGET_REQUEST,
    MEMORY_FORGET_RESPONSE,
    MEMORY_MANAGE_REQUEST,
    MEMORY_MANAGE_RESPONSE,
    MEMORY_SEARCH_REQUEST,
    MEMORY_SEARCH_RESPONSE,
    MEMORY_TIMELINE_REQUEST,
    MEMORY_TIMELINE_RESPONSE,
    MEMORY_GET_REQUEST,
    MEMORY_GET_RESPONSE,
    MEMORY_REMEMBER_REQUEST,
    MEMORY_REMEMBER_RESPONSE,
    SYSTEM_ERROR,
    SYSTEM_PING_REQUEST,
    SYSTEM_PING_RESPONSE,
)


class KittMemoryUnavailable(RuntimeError):
    pass


class KittMemoryClient:
    def __init__(self, address: str | None = None, token_path: str | Path | None = None, timeout: float = 0.75):
        self.address = address or os.getenv("KITT_MEMORY_ADDR", "127.0.0.1:41829")
        if os.name == "nt":
            root = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support"
        else:
            root = Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        self.token_path = Path(token_path or os.getenv("KITT_MEMORY_TOKEN_PATH") or (root / "kitt" / "memory" / "auth.token"))
        self.timeout = max(0.05, min(float(timeout), 10.0))

    def _split_address(self) -> tuple[str, int]:
        raw = self.address.strip()
        try:
            if raw.startswith("["):
                end = raw.index("]")
                host, port_text = raw[1:end], raw[end + 2 :]
            else:
                host, port_text = raw.rsplit(":", 1)
            port = int(port_text)
        except (ValueError, TypeError) as exc:
            raise KittMemoryUnavailable(f"invalid KITT_MEMORY_ADDR: {self.address!r}") from exc
        if host.lower() != "localhost":
            try:
                ip = ipaddress.ip_address(host.split("%", 1)[0])
            except ValueError as exc:
                raise KittMemoryUnavailable("KITT_MEMORY_ADDR must be loopback") from exc
            if not ip.is_loopback:
                raise KittMemoryUnavailable("KITT_MEMORY_ADDR must be loopback")
        if not 1 <= port <= 65535:
            raise KittMemoryUnavailable("invalid KITT_MEMORY_ADDR port")
        return host, port

    def _read_token(self) -> str:
        try:
            token = self.token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise KittMemoryUnavailable(f"kitt-memoryd token unavailable at {self.token_path}") from exc
        if len(token) < 48 or not all(ch in "0123456789abcdefABCDEF" for ch in token):
            raise KittMemoryUnavailable("invalid kitt-memoryd token")
        return token

    @staticmethod
    def _read_line(sock: socket.socket) -> bytes:
        data = bytearray()
        while True:
            if len(data) > MAX_FRAME_BYTES:
                raise KittMemoryUnavailable("kitt-memoryd response exceeds frame limit")
            chunk = sock.recv(min(65536, MAX_FRAME_BYTES + 1 - len(data)))
            if not chunk:
                raise KittMemoryUnavailable("kitt-memoryd closed connection")
            data.extend(chunk)
            pos = data.find(b"\n")
            if pos >= 0:
                return bytes(data[:pos]).rstrip(b"\r")

    def _start_local_service(self) -> None:
        if os.getenv("KITT_MEMORY_AUTOSTART", "1").strip().lower() in {"0", "false", "no", "off"}:
            return
        binary = os.getenv("KITT_MEMORYD_BIN") or shutil.which("kitt-memoryd")
        if not binary:
            return
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        else:
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen([binary], **kwargs)
        except OSError:
            return

    def _call_once(self, kind: str, payload: dict[str, Any], expected_kind: str) -> Any:
        request = Envelope(kind=kind, payload=payload)
        frame = AuthenticatedFrame(token=self._read_token(), envelope=request)
        host, port = self._split_address()
        try:
            with socket.create_connection((host, port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                wire = frame.dumps().encode("utf-8") + b"\n"
                if len(wire) > MAX_FRAME_BYTES:
                    raise KittMemoryUnavailable("request exceeds frame limit")
                sock.sendall(wire)
                raw = self._read_line(sock)
        except (OSError, TimeoutError) as exc:
            raise KittMemoryUnavailable(str(exc)) from exc
        try:
            response = Envelope.loads(raw)
        except Exception as exc:
            raise KittMemoryUnavailable(f"invalid kitt-memoryd response: {exc}") from exc
        if response.correlation_id != request.id:
            raise KittMemoryUnavailable("kitt-memoryd correlation mismatch")
        if response.kind == SYSTEM_ERROR:
            body = response.payload if isinstance(response.payload, dict) else {}
            raise KittMemoryUnavailable(f"{body.get('code', 'memory_error')}: {body.get('message', '')}".strip())
        if response.kind != expected_kind:
            raise KittMemoryUnavailable(f"unexpected response kind {response.kind!r}")
        return response.payload

    def _call(self, kind: str, payload: dict[str, Any], expected_kind: str) -> Any:
        first_error: Exception | None = None
        try:
            return self._call_once(kind, payload, expected_kind)
        except KittMemoryUnavailable as exc:
            first_error = exc
        self._start_local_service()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            time.sleep(0.05)
            try:
                return self._call_once(kind, payload, expected_kind)
            except KittMemoryUnavailable:
                continue
        raise KittMemoryUnavailable(str(first_error or "kitt-memoryd unavailable"))

    def ping(self) -> dict[str, Any]:
        body = self._call(SYSTEM_PING_REQUEST, {}, SYSTEM_PING_RESPONSE)
        if not isinstance(body, dict) or not body.get("ok"):
            raise KittMemoryUnavailable("kitt-memoryd ping failed")
        return body

    def manage(self, operation: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        body = self._call(MEMORY_MANAGE_REQUEST, {"operation": operation, "arguments": dict(arguments or {})}, MEMORY_MANAGE_RESPONSE)
        if not isinstance(body, dict):
            raise KittMemoryUnavailable("invalid memory.manage response")
        return body

    def remember(
        self,
        workspace_id: str,
        content: str,
        kind: str = "PROJECT_RULE",
        pinned: bool = True,
        *,
        namespace: str = "agent-cli",
        sensitivity: str = "private",
        scope: str = "workspace",
        scope_key: str | None = None,
        importance: float | None = None,
        confidence: float = 1.0,
        ttl_seconds: int | None = None,
    ) -> str:
        if scope == "conversation" and not scope_key:
            raise ValueError("conversation scope requires scope_key")
        body = self._call(
            MEMORY_REMEMBER_REQUEST,
            {
                "namespace": namespace,
                "workspace_id": workspace_id,
                "content": content,
                "kind": kind,
                "sensitivity": sensitivity,
                "scope": scope,
                "scope_key": scope_key,
                "importance": (0.9 if pinned else 0.6) if importance is None else float(importance),
                "confidence": float(confidence),
                "pinned": bool(pinned),
                "ttl_seconds": ttl_seconds,
            },
            MEMORY_REMEMBER_RESPONSE,
        )
        memory_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(memory_id, str):
            raise KittMemoryUnavailable("memory.remember response missing id")
        return memory_id

    def search(
        self,
        workspace_id: str,
        query: str,
        *,
        max_results: int = 24,
        token_budget: int = 1200,
        namespace: str = "agent-cli",
        scope_key: str | None = None,
        as_of: int | None = None,
        allow_private: bool = True,
        allow_secret: bool = False,
    ) -> tuple[list[dict[str, Any]], str]:
        body = self._call(
            MEMORY_SEARCH_REQUEST,
            {
                "namespace": namespace,
                "workspace_id": workspace_id,
                "scope_key": scope_key,
                "query": query,
                "max_results": max(1, min(int(max_results), 128)),
                "token_budget": max(1, min(int(token_budget), 65_536)),
                "as_of": as_of,
                "allow_private": bool(allow_private),
                "allow_secret": bool(allow_secret),
            },
            MEMORY_SEARCH_RESPONSE,
        )
        hits = body.get("hits") if isinstance(body, dict) else None
        if not isinstance(hits, list):
            raise KittMemoryUnavailable("memory.search response missing hits")
        return (
            [row for row in hits if isinstance(row, dict)],
            str(body.get("recall_trace_id") or ""),
        )

    def get(
        self,
        workspace_id: str,
        ids: list[str] | tuple[str, ...],
        *,
        token_budget: int = 2400,
        namespace: str = "agent-cli",
        scope_key: str | None = None,
        allow_private: bool = True,
        allow_secret: bool = False,
    ) -> tuple[list[dict[str, Any]], str, list[str]]:
        body = self._call(
            MEMORY_GET_REQUEST,
            {
                "namespace": namespace,
                "workspace_id": workspace_id,
                "scope_key": scope_key,
                "ids": [str(value) for value in ids if str(value).strip()][:128],
                "token_budget": max(1, min(int(token_budget), 65_536)),
                "allow_private": bool(allow_private),
                "allow_secret": bool(allow_secret),
            },
            MEMORY_GET_RESPONSE,
        )
        records = body.get("records") if isinstance(body, dict) else None
        if not isinstance(records, list):
            raise KittMemoryUnavailable("memory.get response missing records")
        truncated = body.get("truncated_ids") if isinstance(body, dict) else []
        return (
            [row for row in records if isinstance(row, dict)],
            str(body.get("recall_trace_id") or ""),
            [str(value) for value in truncated if isinstance(value, str)],
        )

    def timeline(
        self,
        workspace_id: str,
        *,
        source_id: str | None = None,
        scope_key: str | None = None,
        around: int | None = None,
        limit: int = 24,
        token_budget: int = 1200,
        namespace: str = "agent-cli",
        allow_private: bool = True,
        allow_secret: bool = False,
    ) -> tuple[list[dict[str, Any]], str]:
        body = self._call(
            MEMORY_TIMELINE_REQUEST,
            {
                "namespace": namespace,
                "workspace_id": workspace_id,
                "source_id": source_id,
                "scope_key": scope_key,
                "around": around,
                "limit": max(1, min(int(limit), 128)),
                "token_budget": max(1, min(int(token_budget), 65_536)),
                "allow_private": bool(allow_private),
                "allow_secret": bool(allow_secret),
            },
            MEMORY_TIMELINE_RESPONSE,
        )
        hits = body.get("hits") if isinstance(body, dict) else None
        if not isinstance(hits, list):
            raise KittMemoryUnavailable("memory.timeline response missing hits")
        return (
            [row for row in hits if isinstance(row, dict)],
            str(body.get("recall_trace_id") or ""),
        )


    def lifecycle(
        self,
        event: str,
        *,
        workspace_id: str,
        source_id: str,
        source_revision: str,
        input_digest: str,
        source_kind: str = "external",
        namespace: str = "agent-cli",
        source_watermark: str = "",
    ) -> dict[str, Any]:
        allowed = {
            "session.started",
            "turn.started",
            "tool.completed",
            "turn.completed",
            "session.ended",
        }
        event_name = str(event or "").strip()
        if event_name not in allowed:
            raise ValueError(f"unsupported memory lifecycle event: {event_name}")
        digest = str(input_digest or "").strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("input_digest must be a SHA-256 hex digest")
        return self.manage(
            "lifecycle.ingest",
            {
                "event": event_name,
                "namespace": str(namespace or "agent-cli"),
                "workspace_id": str(workspace_id),
                "source_id": str(source_id),
                "source_revision": str(source_revision),
                "source_watermark": str(source_watermark or ""),
                "input_digest": digest,
                "source_kind": str(source_kind or "external"),
            },
        )

    def forget(self, memory_id: str) -> bool:
        body = self._call(MEMORY_FORGET_REQUEST, {"id": memory_id}, MEMORY_FORGET_RESPONSE)
        return bool(body.get("deleted")) if isinstance(body, dict) else False


SharedMemoryClient = KittMemoryClient
SharedMemoryUnavailable = KittMemoryUnavailable
