"""MCP Transports: Stdio subprocess transport and InProcess mock transport."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from typing import Any, Callable, Dict, Optional, Protocol

from kitt.extensions.errors import MCPTransportError

logger = logging.getLogger("kitt.extensions.mcp.transport")


class MCPTransport(Protocol):
    """Protocol for bi-directional JSON-RPC communication."""

    async def connect(self) -> None:
        ...

    async def send(self, message: Dict[str, Any]) -> None:
        ...

    async def receive(self) -> Dict[str, Any]:
        ...

    async def close(self) -> None:
        ...


class StdioTransport:
    """Subprocess stdio transport with structured process lifecycle and cleanup."""

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

    async def connect(self) -> None:
        full_env = dict(os.environ)
        full_env.update(self.env)
        cmd_list = [self.command] + self.args

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd_list,
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
            raise MCPTransportError("Stdio transport is not connected.")

        try:
            payload = json.dumps(message) + "\n"
            self._process.stdin.write(payload.encode("utf-8"))
            await self._process.stdin.drain()
        except Exception as exc:
            raise MCPTransportError(f"Failed to write to stdio transport: {exc}") from exc

    async def receive(self) -> Dict[str, Any]:
        if not self._process or not self._process.stdout:
            raise MCPTransportError("Stdio transport is not connected.")

        line = await self._process.stdout.readline()
        if not line:
            # Check stderr for diagnostic error
            err_msg = ""
            if self._process.stderr:
                try:
                    err_bytes = await asyncio.wait_for(self._process.stderr.read(1024), timeout=0.1)
                    err_msg = err_bytes.decode("utf-8", errors="replace")
                except Exception:
                    pass
            raise MCPTransportError(f"Stdio transport EOF from server. {err_msg}".strip())

        try:
            return json.loads(line.decode("utf-8"))
        except Exception as exc:
            raise MCPTransportError(f"Malformed JSON-RPC line from stdio transport: {exc}") from exc

    async def close(self) -> None:
        if not self._process:
            return

        proc = self._process
        self._process = None

        if proc.returncode is not None:
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
            logger.warning("Error during stdio transport shutdown: %s", exc)


class InProcessTransport:
    """In-process mock transport for standalone testing without child processes."""

    def __init__(self, handler_fn: Callable[[Dict[str, Any]], Dict[str, Any]]):
        self.handler_fn = handler_fn
        self._incoming: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._is_closed = False

    async def connect(self) -> None:
        self._is_closed = False

    async def send(self, message: Dict[str, Any]) -> None:
        if self._is_closed:
            raise MCPTransportError("InProcess transport is closed.")
        resp = self.handler_fn(message)
        if resp is not None:
            await self._incoming.put(resp)

    async def receive(self) -> Dict[str, Any]:
        if self._is_closed:
            raise MCPTransportError("InProcess transport is closed.")
        return await self._incoming.get()

    async def close(self) -> None:
        self._is_closed = True
