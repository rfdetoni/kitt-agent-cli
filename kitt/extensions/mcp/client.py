"""MCP JSON-RPC 2.0 client with single-owner event-loop lifecycle."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from typing import Any, Dict, List, Optional

from kitt.extensions.errors import (
    MCPError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPTransportError,
)
from kitt.extensions.mcp.models import (
    MCPPrompt,
    MCPResource,
    MCPServerConfig,
    MCPServerState,
    MCPTool,
)
from kitt.extensions.mcp.transport import MCPTransport

logger = logging.getLogger("kitt.extensions.mcp.client")


class MCPClient:
    def __init__(
        self,
        config: MCPServerConfig,
        transport: MCPTransport,
    ):
        self.config = config
        self.transport = transport
        self.state = MCPServerState.DISCONNECTED
        self._request_id = 0
        self._pending_requests: Dict[
            int, asyncio.Future[Dict[str, Any]]
        ] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._server_info: Dict[str, Any] = {}
        self._server_capabilities: Dict[str, Any] = {}
        self._owner_loop: Optional[
            asyncio.AbstractEventLoop
        ] = None

    @property
    def owner_loop(
        self,
    ) -> Optional[asyncio.AbstractEventLoop]:
        return self._owner_loop

    async def connect(self) -> None:
        current_loop = asyncio.get_running_loop()
        if self.state == MCPServerState.CONNECTED:
            if self._owner_loop is not current_loop:
                raise MCPError(
                    "MCP client is already connected on another event loop"
                )
            return

        self._owner_loop = current_loop
        self.state = MCPServerState.CONNECTING
        try:
            await self.transport.connect()
            self._reader_task = asyncio.create_task(
                self._read_loop()
            )
            init_params = {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {"listChanged": True},
                    "prompts": {"listChanged": True},
                },
                "clientInfo": {
                    "name": "KITT Agent CLI",
                    "version": "1.0.0",
                },
            }
            response = await self._send_request(
                "initialize",
                init_params,
                timeout=self.config.timeout_seconds,
            )
            self._server_info = response.get("serverInfo", {})
            self._server_capabilities = response.get(
                "capabilities", {}
            )
            await self.transport.send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            )
            self.state = MCPServerState.CONNECTED
        except Exception as exc:
            self.state = MCPServerState.FAILED
            try:
                await self.disconnect()
            except Exception:
                logger.debug(
                    "MCP cleanup after failed connect failed",
                    exc_info=True,
                )
            raise MCPError(
                f"Failed to initialize MCP server "
                f"'{self.config.server_id}': {exc}"
            ) from exc

    async def disconnect(self) -> None:
        self.state = MCPServerState.STOPPING
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug(
                    "MCP reader task shutdown failed",
                    exc_info=True,
                )

        for future in tuple(self._pending_requests.values()):
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

        await self.transport.close()
        self.state = MCPServerState.DISCONNECTED
        self._owner_loop = None

    def call_tool_sync(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> str:
        """Bridge ToolRegistry worker threads to the MCP owner loop."""
        loop = self._owner_loop
        if (
            loop is None
            or not loop.is_running()
            or self.state != MCPServerState.CONNECTED
        ):
            raise MCPError(
                f"MCP server '{self.config.server_id}' is not connected"
            )

        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            raise MCPError(
                "Synchronous MCP tool dispatch cannot block the MCP owner "
                "event loop; execute the KITT turn on a worker thread"
            )

        future = asyncio.run_coroutine_threadsafe(
            self.call_tool(tool_name, arguments),
            loop,
        )
        try:
            return future.result(
                timeout=max(
                    1.0,
                    self.config.timeout_seconds + 1.0,
                )
            )
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise MCPTimeoutError(
                f"MCP tool '{tool_name}' timed out after "
                f"{self.config.timeout_seconds}s"
            ) from exc

    async def list_tools(self) -> List[MCPTool]:
        response = await self._send_request("tools/list", {})
        tools: List[MCPTool] = []
        for item in response.get("tools", []):
            if not isinstance(item, dict) or "name" not in item:
                continue
            name = str(item["name"])
            if (
                self.config.deny_tools
                and name in self.config.deny_tools
            ):
                continue
            if (
                self.config.allow_tools is not None
                and name not in self.config.allow_tools
            ):
                continue
            tools.append(
                MCPTool(
                    server_id=self.config.server_id,
                    name=name,
                    description=item.get("description", ""),
                    input_schema=item.get("inputSchema", {}),
                )
            )
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> str:
        response = await self._send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=self.config.timeout_seconds,
        )
        text_parts = [
            item.get("text", "")
            for item in response.get("content", [])
            if isinstance(item, dict)
            and item.get("type") == "text"
        ]
        result_text = (
            "\n".join(text_parts)
            if text_parts
            else json.dumps(response)
        )
        encoded = result_text.encode("utf-8")
        if len(encoded) > self.config.max_output_bytes:
            result_text = (
                encoded[: self.config.max_output_bytes]
                .decode("utf-8", "ignore")
                + "\n... [TRUNCATED: MCP Output Exceeded Max Bounds]"
            )
        if response.get("isError"):
            raise MCPError(
                f"MCP tool '{tool_name}' returned error: {result_text}"
            )
        return result_text

    async def list_resources(self) -> List[MCPResource]:
        response = await self._send_request(
            "resources/list", {}
        )
        resources: List[MCPResource] = []
        for item in response.get("resources", []):
            if isinstance(item, dict) and "uri" in item:
                resources.append(
                    MCPResource(
                        server_id=self.config.server_id,
                        uri=item["uri"],
                        name=item.get("name", item["uri"]),
                        description=item.get("description", ""),
                        mime_type=item.get("mimeType"),
                    )
                )
        return resources

    async def read_resource(self, uri: str) -> str:
        response = await self._send_request(
            "resources/read", {"uri": uri}
        )
        return "\n".join(
            item["text"]
            for item in response.get("contents", [])
            if isinstance(item, dict) and "text" in item
        )

    async def list_prompts(self) -> List[MCPPrompt]:
        response = await self._send_request(
            "prompts/list", {}
        )
        prompts: List[MCPPrompt] = []
        for item in response.get("prompts", []):
            if isinstance(item, dict) and "name" in item:
                prompts.append(
                    MCPPrompt(
                        server_id=self.config.server_id,
                        name=item["name"],
                        description=item.get("description", ""),
                        arguments=item.get("arguments", []),
                    )
                )
        return prompts

    async def _send_request(
        self,
        method: str,
        params: Dict[str, Any],
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        if (
            self._owner_loop is not None
            and asyncio.get_running_loop() is not self._owner_loop
        ):
            raise MCPError(
                "MCP request attempted from a non-owner event loop"
            )

        self._request_id += 1
        request_id = self._request_id
        future: asyncio.Future[
            Dict[str, Any]
        ] = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        try:
            await self.transport.send(payload)
            return await asyncio.wait_for(
                future, timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            raise MCPTimeoutError(
                f"MCP request '{method}' (id={request_id}) timed out "
                f"after {timeout}s."
            ) from exc
        finally:
            self._pending_requests.pop(request_id, None)

    async def _read_loop(self) -> None:
        try:
            while True:
                message = await self.transport.receive()
                request_id = message.get("id")
                if request_id not in self._pending_requests:
                    continue
                future = self._pending_requests[request_id]
                if future.done():
                    continue
                if "error" in message:
                    error = message["error"]
                    future.set_exception(
                        MCPProtocolError(
                            "MCP server error "
                            f"({error.get('code', 'unknown')}): "
                            f"{error.get('message', '')}"
                        )
                    )
                else:
                    future.set_result(
                        message.get("result", {})
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state = MCPServerState.DEGRADED
            wrapped = MCPTransportError(
                f"MCP reader loop failed: {exc}"
            )
            for future in tuple(
                self._pending_requests.values()
            ):
                if not future.done():
                    future.set_exception(wrapped)
            logger.debug(
                "MCP read loop ended with error: %s",
                exc,
            )
