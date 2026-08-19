"""MCP transports with bounded environment inheritance and structured lifecycle."""
from __future__ import annotations

import asyncio
import json
import logging
import os
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
