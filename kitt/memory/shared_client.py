"""Protocol-v1 client for shared KITT memory owned by kittd."""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from typing import Any

from kitt_protocol import (
    AuthenticatedFrame,
    Envelope,
    MAX_FRAME_BYTES,
    MEMORY_RECALL_REQUEST,
    MEMORY_RECALL_RESPONSE,
    MEMORY_REMEMBER_REQUEST,
    MEMORY_REMEMBER_RESPONSE,
    SYSTEM_ERROR,
)


class SharedMemoryUnavailable(RuntimeError):
    """Raised when the optional shared-memory daemon cannot serve a valid v1 response."""


class SharedMemoryClient:
    def __init__(
        self,
        address: str | None = None,
        token_path: str | Path | None = None,
        timeout: float = 0.5,
    ):
        self.address = address or os.getenv("KITT_DAEMON_ADDR", "127.0.0.1:41827")
        if os.name == "nt":
            config_root = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        elif sys.platform == "darwin":
            config_root = Path.home() / "Library" / "Application Support"
        else:
            config_root = Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        default = config_root / "kitt" / "assistant" / "auth.token"
        self.token_path = Path(token_path) if token_path else default
        self.timeout = max(0.05, min(float(timeout), 10.0))

    def _call(self, kind: str, payload: dict[str, Any], expected_kind: str) -> Any:
        token = self._read_token()
        request = Envelope(kind=kind, payload=payload)
        frame = AuthenticatedFrame(token=token, envelope=request)
        host, port = self._split_address()

        try:
            with socket.create_connection((host, port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                wire = frame.dumps().encode("utf-8") + b"\n"
                if len(wire) > MAX_FRAME_BYTES:
                    raise SharedMemoryUnavailable("request exceeds KITT protocol frame limit")
                sock.sendall(wire)
                raw = self._read_line(sock)
        except (OSError, TimeoutError) as exc:
            raise SharedMemoryUnavailable(str(exc)) from exc

        try:
            response = Envelope.loads(raw)
        except Exception as exc:
            raise SharedMemoryUnavailable(f"invalid kittd response: {exc}") from exc

        if response.correlation_id != request.id:
            raise SharedMemoryUnavailable("kittd response correlation mismatch")
        if response.kind == SYSTEM_ERROR:
            payload_obj = response.payload if isinstance(response.payload, dict) else {}
            code = str(payload_obj.get("code", "unknown"))
            message = str(payload_obj.get("message", ""))
            raise SharedMemoryUnavailable(f"{code}: {message}".strip())
        if response.kind != expected_kind:
            raise SharedMemoryUnavailable(
                f"unexpected kittd response kind {response.kind!r}; expected {expected_kind!r}"
            )
        return response.payload

    def _read_token(self) -> str:
        try:
            token = self.token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SharedMemoryUnavailable(f"cannot read kittd token: {exc}") from exc
        if len(token) < 48 or not all(ch in "0123456789abcdefABCDEF" for ch in token):
            raise SharedMemoryUnavailable("kittd token is missing or invalid")
        return token

    def _split_address(self) -> tuple[str, int]:
        try:
            host, port_text = self.address.rsplit(":", 1)
            port = int(port_text)
        except (ValueError, TypeError) as exc:
            raise SharedMemoryUnavailable(f"invalid KITT_DAEMON_ADDR: {self.address!r}") from exc
        if not host or not 1 <= port <= 65535:
            raise SharedMemoryUnavailable(f"invalid KITT_DAEMON_ADDR: {self.address!r}")
        return host, port

    @staticmethod
    def _read_line(sock: socket.socket) -> bytes:
        data = bytearray()
        while True:
            if len(data) > MAX_FRAME_BYTES:
                raise SharedMemoryUnavailable("kittd response exceeds frame limit")
            remaining = MAX_FRAME_BYTES + 1 - len(data)
            chunk = sock.recv(min(65536, remaining))
            if not chunk:
                raise SharedMemoryUnavailable("kittd closed connection before response")
            data.extend(chunk)
            newline = data.find(b"\n")
            if newline >= 0:
                line = bytes(data[:newline]).rstrip(b"\r")
                if len(line) > MAX_FRAME_BYTES:
                    raise SharedMemoryUnavailable("kittd response exceeds frame limit")
                return line

    def remember(
        self,
        workspace_id: str,
        content: str,
        kind: str = "PROJECT_RULE",
        pinned: bool = True,
    ) -> str:
        result = self._call(
            MEMORY_REMEMBER_REQUEST,
            {
                "namespace": "agent-cli",
                "workspace_id": workspace_id,
                "content": content,
                "kind": kind.strip().lower(),
                "sensitivity": "private",
                "scope": "workspace",
                "importance": 0.8,
                "confidence": 1.0,
                "pinned": pinned,
                "ttl_seconds": None,
            },
            MEMORY_REMEMBER_RESPONSE,
        )
        if not isinstance(result, dict) or not isinstance(result.get("id"), str):
            raise SharedMemoryUnavailable("memory.remember response missing id")
        return result["id"]

    def recall(
        self,
        workspace_id: str,
        query: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        result = self._call(
            MEMORY_RECALL_REQUEST,
            {
                "namespace": "agent-cli",
                "workspace_id": workspace_id,
                "query": query,
                "limit": max(1, min(int(limit), 50)),
                "allow_private": True,
                "allow_secret": False,
            },
            MEMORY_RECALL_RESPONSE,
        )
        if not isinstance(result, dict) or not isinstance(result.get("records"), list):
            raise SharedMemoryUnavailable("memory.recall response missing records")
        return [record for record in result["records"] if isinstance(record, dict)]
