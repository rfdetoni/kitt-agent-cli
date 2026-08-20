"""MCP transports with bounded environment inheritance and structured lifecycle."""
from __future__ import annotations

import asyncio
import json
import ipaddress
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Protocol

from kitt.extensions.errors import MCPTransportError

logger = logging.getLogger("kitt.extensions.mcp.transport")


class MCPTransport(Protocol):
    async def connect(self) -> None: ...
    async def send(self, message: Dict[str, Any]) -> None: ...
    async def receive(self) -> Dict[str, Any]: ...
    async def close(self) -> None: ...


class StdioTransport:
    def __init__(self, command: str, args: Optional[list[str]] = None,
                 env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None):
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd
        self._process: Optional[asyncio.subprocess.Process] = None

    @staticmethod
    def _base_env() -> Dict[str, str]:
        # Secrets are never inherited implicitly. MCP-specific credentials must
        # be declared explicitly in the server config env block.
        keep = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL", "HOME", "USERPROFILE"}
        return {k: v for k, v in os.environ.items() if k in keep}

    async def connect(self) -> None:
        if not self.command:
            raise MCPTransportError("MCP stdio command is required")
        full_env = self._base_env()
        full_env.update({str(k): str(v) for k, v in self.env.items()})
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.command, *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
                cwd=self.cwd,
            )
        except Exception as exc:
            raise MCPTransportError(f"Failed to spawn MCP stdio server '{self.command}': {exc}") from exc

    async def send(self, message: Dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            raise MCPTransportError("Stdio transport is not connected")
        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        if len(payload) > 2 * 1024 * 1024:
            raise MCPTransportError("MCP request exceeds 2 MB transport limit")
        self._process.stdin.write(payload)
        await self._process.stdin.drain()

    async def receive(self) -> Dict[str, Any]:
        if not self._process or not self._process.stdout:
            raise MCPTransportError("Stdio transport is not connected")
        line = await self._process.stdout.readline()
        if not line:
            err = ""
            if self._process.stderr:
                try:
                    err = (await asyncio.wait_for(self._process.stderr.read(4096), timeout=0.1)).decode("utf-8", "replace")
                except Exception:
                    pass
            raise MCPTransportError(f"Stdio transport EOF. {err}".strip())
        if len(line) > 2 * 1024 * 1024:
            raise MCPTransportError("MCP response exceeds 2 MB transport limit")
        try:
            payload = json.loads(line.decode("utf-8"))
        except Exception as exc:
            raise MCPTransportError(f"Malformed MCP JSON-RPC line: {exc}") from exc
        if not isinstance(payload, dict):
            raise MCPTransportError("MCP JSON-RPC response must be an object")
        return payload

    async def close(self) -> None:
        proc, self._process = self._process, None
        if not proc or proc.returncode is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except Exception as exc:
            logger.warning("Error during MCP transport shutdown: %s", exc)


class InProcessTransport:
    def __init__(self, handler_fn: Callable[[Dict[str, Any]], Dict[str, Any]]):
        self.handler_fn = handler_fn
        self._incoming: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._is_closed = False

    async def connect(self) -> None:
        self._is_closed = False

    async def send(self, message: Dict[str, Any]) -> None:
        if self._is_closed:
            raise MCPTransportError("InProcess transport is closed")
        resp = self.handler_fn(message)
        if resp is not None:
            await self._incoming.put(resp)

    async def receive(self) -> Dict[str, Any]:
        if self._is_closed:
            raise MCPTransportError("InProcess transport is closed")
        return await self._incoming.get()

    async def close(self) -> None:
        self._is_closed = True


class HTTPTransport:
    _MAX_MESSAGE_BYTES = 2 * 1024 * 1024

    def __init__(self, url: str, timeout_seconds: float = 30.0):
        self.url = str(url or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self._incoming: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._session_id: Optional[str] = None
        self._closed = False
        self._validated_url: Optional[str] = None

    @staticmethod
    def _is_loopback_host(hostname: str) -> bool:
        host = str(hostname or "").strip().lower()
        if host in {"localhost", "127.0.0.1", "::1", "[::1]"}:
            return True
        try:
            return ipaddress.ip_address(host.strip("[]")).is_loopback
        except ValueError:
            return False

    def _validate_url(self) -> str:
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise MCPTransportError(f"Invalid MCP HTTP URL: {self.url}")
        if parsed.scheme == "http" and not self._is_loopback_host(parsed.hostname or ""):
            raise MCPTransportError(
                "Remote MCP HTTP requires HTTPS; plain HTTP is allowed only on loopback"
            )
        return urllib.parse.urlunsplit(parsed)

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2024-11-05",
        }
        if self._session_id:
            headers["MCP-Session-Id"] = self._session_id
        return headers

    @staticmethod
    def _bounded_read(response) -> bytes:
        payload = response.read(HTTPTransport._MAX_MESSAGE_BYTES + 1)
        if len(payload) > HTTPTransport._MAX_MESSAGE_BYTES:
            raise MCPTransportError("MCP response exceeds 2 MB transport limit")
        return payload

    @staticmethod
    def _parse_sse_event(data_lines: list[str]) -> Optional[Dict[str, Any]]:
        joined = "\n".join(data_lines).strip()
        if not joined:
            return None
        try:
            candidate = json.loads(joined)
        except Exception as exc:
            raise MCPTransportError(
                f"Malformed MCP SSE JSON payload: {exc}"
            ) from exc
        if candidate is None:
            return None
        if not isinstance(candidate, dict):
            raise MCPTransportError("MCP SSE response must be an object")
        return candidate

    @classmethod
    def _read_sse_message(cls, response, expected_id: Any) -> Optional[Dict[str, Any]]:
        total = 0
        event_data: list[str] = []
        last_message: Optional[Dict[str, Any]] = None
        while True:
            raw_bytes = response.readline(cls._MAX_MESSAGE_BYTES + 1)
            if not raw_bytes:
                break
            total += len(raw_bytes)
            if total > cls._MAX_MESSAGE_BYTES:
                raise MCPTransportError("MCP response exceeds 2 MB transport limit")
            raw_line = raw_bytes.decode("utf-8")
            line = raw_line.rstrip("\r")
            if not line:
                if event_data:
                    candidate = cls._parse_sse_event(event_data)
                    event_data = []
                    if candidate is not None:
                        last_message = candidate
                        if (
                            expected_id is None
                            or candidate.get("id") == expected_id
                            or "result" in candidate
                            or "error" in candidate
                        ):
                            return candidate
                continue
            if line.startswith("data:"):
                event_data.append(line[5:].lstrip())
        if event_data:
            candidate = cls._parse_sse_event(event_data)
            if candidate is not None:
                last_message = candidate
        return last_message

    def _do_post(self, payload: bytes, expected_id: Any) -> Optional[Dict[str, Any]]:
        request = urllib.request.Request(
            self._validated_url or self.url,
            data=payload,
            headers=self._request_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                session_id = response.headers.get("MCP-Session-Id")
                if session_id:
                    self._session_id = session_id
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/event-stream" in content_type:
                    message = self._read_sse_message(response, expected_id)
                else:
                    raw = self._bounded_read(response)
                    if not raw:
                        return None
                    try:
                        message = json.loads(raw.decode("utf-8"))
                    except Exception as exc:
                        raise MCPTransportError(
                            f"Malformed MCP HTTP JSON response: {exc}"
                        ) from exc
                if message is None:
                    return None
                if not isinstance(message, dict):
                    raise MCPTransportError("MCP HTTP response must be an object")
                return message
        except urllib.error.URLError as exc:
            raise MCPTransportError(f"MCP HTTP request failed: {exc}") from exc

    def _do_delete(self) -> None:
        request = urllib.request.Request(
            self._validated_url or self.url,
            headers=self._request_headers(),
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds):
                return
        except Exception:
            return

    async def connect(self) -> None:
        if not self.url:
            raise MCPTransportError("MCP HTTP URL is required")
        self._validated_url = self._validate_url()
        self._closed = False

    async def send(self, message: Dict[str, Any]) -> None:
        if self._closed:
            raise MCPTransportError("HTTP transport is closed")
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        if len(payload) > self._MAX_MESSAGE_BYTES:
            raise MCPTransportError("MCP request exceeds 2 MB transport limit")
        response = await asyncio.to_thread(self._do_post, payload, message.get("id"))
        if response is not None and "id" in message:
            await self._incoming.put(response)

    async def receive(self) -> Dict[str, Any]:
        if self._closed:
            raise MCPTransportError("HTTP transport is closed")
        return await self._incoming.get()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._session_id:
            await asyncio.to_thread(self._do_delete)
