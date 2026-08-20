"""MCP transports with bounded, fail-closed lifecycle."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
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
    def __init__(
        self,
        command: str,
        args: Optional[list[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ):
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd
        self._process: Optional[asyncio.subprocess.Process] = None

    @staticmethod
    def _base_env() -> Dict[str, str]:
        keep = {
            "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
            "LANG", "LC_ALL", "HOME", "USERPROFILE",
        }
        return {k: v for k, v in os.environ.items() if k in keep}

    async def connect(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return
        if not self.command:
            raise MCPTransportError("MCP stdio command is required")
        full_env = self._base_env()
        full_env.update({str(k): str(v) for k, v in self.env.items()})
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
                cwd=self.cwd,
            )
        except Exception as exc:
            raise MCPTransportError(
                f"Failed to spawn MCP stdio server '{self.command}': {exc}"
            ) from exc

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
            error = ""
            if self._process.stderr:
                try:
                    error = (
                        await asyncio.wait_for(
                            self._process.stderr.read(4096), timeout=0.1
                        )
                    ).decode("utf-8", "replace")
                except Exception:
                    pass
            raise MCPTransportError(f"Stdio transport EOF. {error}".strip())
        if len(line) > 2 * 1024 * 1024:
            raise MCPTransportError("MCP response exceeds 2 MB transport limit")
        try:
            payload = json.loads(line.decode("utf-8"))
        except Exception as exc:
            raise MCPTransportError(
                f"Malformed MCP JSON-RPC line: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise MCPTransportError("MCP JSON-RPC response must be an object")
        return payload

    async def close(self) -> None:
        process, self._process = self._process, None
        if not process or process.returncode is not None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        except Exception as exc:
            logger.warning("Error during MCP transport shutdown: %s", exc)


class InProcessTransport:
    def __init__(
        self,
        handler_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    ):
        self.handler_fn = handler_fn
        self._incoming: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._is_closed = False

    async def connect(self) -> None:
        self._is_closed = False

    async def send(self, message: Dict[str, Any]) -> None:
        if self._is_closed:
            raise MCPTransportError("InProcess transport is closed")
        response = self.handler_fn(message)
        if response is not None:
            await self._incoming.put(response)

    async def receive(self) -> Dict[str, Any]:
        if self._is_closed:
            raise MCPTransportError("InProcess transport is closed")
        return await self._incoming.get()

    async def close(self) -> None:
        self._is_closed = True


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MCPTransportError(
            "MCP HTTP redirects are disabled; configure the final endpoint "
            f"directly ({newurl})"
        )


class HTTPTransport:
    _MAX_MESSAGE_BYTES = 2 * 1024 * 1024

    def __init__(
        self,
        url: str,
        timeout_seconds: float = 30.0,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.url = str(url or "").strip()
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.headers = {
            str(key): str(value) for key, value in (headers or {}).items()
        }
        for key, value in self.headers.items():
            if (
                not key
                or "\r" in key
                or "\n" in key
                or "\r" in value
                or "\n" in value
            ):
                raise MCPTransportError(
                    "MCP HTTP headers contain invalid characters"
                )
        self._incoming: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._session_id: Optional[str] = None
        self._closed = True
        self._validated_url: Optional[str] = None
        self._send_lock: Optional[asyncio.Lock] = None
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    @staticmethod
    def _is_loopback_address(address: str) -> bool:
        try:
            return ipaddress.ip_address(address.strip("[]")).is_loopback
        except ValueError:
            return False

    @classmethod
    def _http_host_is_loopback(cls, hostname: str, port: int) -> bool:
        host = str(hostname or "").strip()
        if cls._is_loopback_address(host):
            return True
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            return False
        addresses = {
            item[4][0]
            for item in infos
            if item and item[4]
        }
        return bool(addresses) and all(
            cls._is_loopback_address(address) for address in addresses
        )

    def _validate_url_sync(self) -> str:
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MCPTransportError(f"Invalid MCP HTTP URL: {self.url}")
        if parsed.username is not None or parsed.password is not None:
            raise MCPTransportError(
                "Credentials in MCP URL are forbidden; use headers"
            )
        if parsed.fragment:
            raise MCPTransportError(
                "MCP HTTP URL must not contain a fragment"
            )
        if parsed.scheme == "http":
            port = parsed.port or 80
            if not self._http_host_is_loopback(parsed.hostname, port):
                raise MCPTransportError(
                    "Remote MCP HTTP requires HTTPS; plain HTTP is "
                    "loopback-only"
                )
        return urllib.parse.urlunsplit(parsed)

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        headers.setdefault(
            "Accept", "application/json, text/event-stream"
        )
        headers.setdefault("Content-Type", "application/json")
        headers["MCP-Protocol-Version"] = "2024-11-05"
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
    def _parse_sse_event(
        data_lines: list[str],
    ) -> Optional[Dict[str, Any]]:
        joined = "\n".join(data_lines).strip()
        if not joined:
            return None
        try:
            candidate = json.loads(joined)
        except Exception as exc:
            raise MCPTransportError(
                f"Malformed MCP SSE JSON payload: {exc}"
            ) from exc
        if not isinstance(candidate, dict):
            raise MCPTransportError("MCP SSE response must be an object")
        return candidate

    @staticmethod
    def _matches_expected_id(
        candidate: Dict[str, Any], expected_id: Any
    ) -> bool:
        if expected_id is None:
            return True
        return candidate.get("id") == expected_id

    @classmethod
    def _read_sse_message(
        cls,
        response,
        expected_id: Any,
    ) -> Optional[Dict[str, Any]]:
        total = 0
        event_data: list[str] = []
        while True:
            raw = response.readline(cls._MAX_MESSAGE_BYTES + 1)
            if not raw:
                break
            total += len(raw)
            if total > cls._MAX_MESSAGE_BYTES:
                raise MCPTransportError(
                    "MCP response exceeds 2 MB transport limit"
                )
            try:
                line = raw.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError as exc:
                raise MCPTransportError(
                    f"Invalid UTF-8 in MCP SSE response: {exc}"
                ) from exc

            if line == "":
                if event_data:
                    candidate = cls._parse_sse_event(event_data)
                    event_data = []
                    if (
                        candidate is not None
                        and cls._matches_expected_id(
                            candidate, expected_id
                        )
                    ):
                        return candidate
                continue

            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                event_data.append(line[5:].lstrip())

        if event_data:
            candidate = cls._parse_sse_event(event_data)
            if (
                candidate is not None
                and cls._matches_expected_id(candidate, expected_id)
            ):
                return candidate
        return None

    def _open(self, request):
        return self._opener.open(
            request, timeout=self.timeout_seconds
        )

    def _do_post(
        self,
        payload: bytes,
        expected_id: Any,
    ) -> Optional[Dict[str, Any]]:
        request = urllib.request.Request(
            self._validated_url or self.url,
            data=payload,
            headers=self._request_headers(),
            method="POST",
        )
        try:
            with self._open(request) as response:
                session_id = response.headers.get("MCP-Session-Id")
                if session_id:
                    self._session_id = session_id
                content_type = response.headers.get(
                    "Content-Type", ""
                ).lower()
                if "text/event-stream" in content_type:
                    return self._read_sse_message(
                        response, expected_id
                    )
                raw = self._bounded_read(response)
                if not raw:
                    return None
                try:
                    message = json.loads(raw.decode("utf-8"))
                except Exception as exc:
                    raise MCPTransportError(
                        f"Malformed MCP HTTP JSON response: {exc}"
                    ) from exc
                if not isinstance(message, dict):
                    raise MCPTransportError(
                        "MCP HTTP response must be an object"
                    )
                if not self._matches_expected_id(
                    message, expected_id
                ):
                    raise MCPTransportError(
                        "MCP HTTP response id does not match request"
                    )
                return message
        except MCPTransportError:
            raise
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(4096).decode("utf-8", "replace")
            except Exception:
                pass
            raise MCPTransportError(
                f"MCP HTTP server returned {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise MCPTransportError(
                f"MCP HTTP request failed: {exc}"
            ) from exc

    def _do_delete(self) -> None:
        request = urllib.request.Request(
            self._validated_url or self.url,
            headers=self._request_headers(),
            method="DELETE",
        )
        try:
            with self._open(request):
                return
        except Exception:
            logger.debug(
                "MCP HTTP session delete failed",
                exc_info=True,
            )

    async def connect(self) -> None:
        if not self.url:
            raise MCPTransportError("MCP HTTP URL is required")
        self._validated_url = await asyncio.to_thread(
            self._validate_url_sync
        )
        self._closed = False
        self._send_lock = asyncio.Lock()
        while not self._incoming.empty():
            try:
                self._incoming.get_nowait()
                self._incoming.task_done()
            except asyncio.QueueEmpty:
                break

    async def send(self, message: Dict[str, Any]) -> None:
        if self._closed:
            raise MCPTransportError("HTTP transport is closed")
        payload = json.dumps(
            message, ensure_ascii=False
        ).encode("utf-8")
        if len(payload) > self._MAX_MESSAGE_BYTES:
            raise MCPTransportError(
                "MCP request exceeds 2 MB transport limit"
            )
        if self._send_lock is None:
            self._send_lock = asyncio.Lock()
        async with self._send_lock:
            response = await asyncio.to_thread(
                self._do_post,
                payload,
                message.get("id"),
            )
        if response is not None and "id" in message:
            await self._incoming.put(response)

    async def receive(self) -> Dict[str, Any]:
        if self._closed:
            raise MCPTransportError("HTTP transport is closed")
        return await self._incoming.get()

    async def close(self) -> None:
        if self._closed:
            return
        if self._session_id:
            await asyncio.to_thread(self._do_delete)
        self._session_id = None
        self._closed = True
        while not self._incoming.empty():
            try:
                self._incoming.get_nowait()
                self._incoming.task_done()
            except asyncio.QueueEmpty:
                break
