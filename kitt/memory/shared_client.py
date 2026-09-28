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
    MEMORY_RECALL_REQUEST,
    MEMORY_RECALL_RESPONSE,
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

    def remember(self, workspace_id: str, content: str, kind: str = "PROJECT_RULE", pinned: bool = True) -> str:
        body = self._call(
            MEMORY_REMEMBER_REQUEST,
            {
                "namespace": "agent-cli",
                "workspace_id": workspace_id,
                "content": content,
                "kind": kind,
                "sensitivity": "private",
                "scope": "workspace",
                "scope_key": None,
                "importance": 0.9 if pinned else 0.6,
                "confidence": 1.0,
                "pinned": bool(pinned),
                "ttl_seconds": None,
            },
            MEMORY_REMEMBER_RESPONSE,
        )
        memory_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(memory_id, str):
            raise KittMemoryUnavailable("memory.remember response missing id")
        return memory_id

    def recall(self, workspace_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        body = self._call(
            MEMORY_RECALL_REQUEST,
            {
                "namespace": "agent-cli",
                "workspace_id": workspace_id,
                "scope_key": None,
                "query": query,
                "limit": max(1, min(int(limit), 50)),
                "as_of": None,
                "allow_private": True,
                "allow_secret": False,
            },
            MEMORY_RECALL_RESPONSE,
        )
        rows = body.get("records") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise KittMemoryUnavailable("memory.recall response missing records")
        return [row for row in rows if isinstance(row, dict)]

    def forget(self, memory_id: str) -> bool:
        body = self._call(MEMORY_FORGET_REQUEST, {"id": memory_id}, MEMORY_FORGET_RESPONSE)
        return bool(body.get("deleted")) if isinstance(body, dict) else False


SharedMemoryClient = KittMemoryClient
SharedMemoryUnavailable = KittMemoryUnavailable
