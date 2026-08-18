"""MCP JSON-RPC 2.0 client managing session handshake, tool invocations, and resources."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from kitt.extensions.errors import MCPError, MCPProtocolError, MCPTimeoutError
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
    """Client implementing Model Context Protocol JSON-RPC 2.0 communication."""

    def __init__(self, config: MCPServerConfig, transport: MCPTransport):
        self.config = config
        self.transport = transport
        self.state = MCPServerState.DISCONNECTED
        self._request_id = 0
        self._pending_requests: Dict[int, asyncio.Future[Dict[str, Any]]] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._server_info: Dict[str, Any] = {}
        self._server_capabilities: Dict[str, Any] = {}

    async def connect(self) -> None:
        """Establishes transport connection and completes MCP initialize handshake."""
        self.state = MCPServerState.CONNECTING
        try:
            await self.transport.connect()
            self._reader_task = asyncio.create_task(self._read_loop())

            # 1. Send initialize request
            init_params = {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {"listChanged": True},
                    "prompts": {"listChanged": True},
                },
                "clientInfo": {"name": "KITT Agent CLI", "version": "1.0.0"},
            }

            resp = await self._send_request("initialize", init_params, timeout=self.config.timeout_seconds)
            self._server_info = resp.get("serverInfo", {})
            self._server_capabilities = resp.get("capabilities", {})

            # 2. Send initialized notification
            await self.transport.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            self.state = MCPServerState.CONNECTED
        except Exception as exc:
            self.state = MCPServerState.FAILED
            await self.disconnect()
            raise MCPError(f"Failed to initialize MCP server '{self.config.server_id}': {exc}") from exc

    async def disconnect(self) -> None:
        """Gracefully terminates MCP connection and cleans up pending requests."""
        self.state = MCPServerState.STOPPING
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None

        for fut in self._pending_requests.values():
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()

        await self.transport.close()
        self.state = MCPServerState.DISCONNECTED

    async def list_tools(self) -> List[MCPTool]:
        """Fetches tools exposed by the MCP server and applies whitelist/blacklist filtering."""
        resp = await self._send_request("tools/list", {})
        tools_raw = resp.get("tools", [])
        tools: List[MCPTool] = []

        for t in tools_raw:
            if not isinstance(t, dict) or "name" not in t:
                continue
            t_name = t["name"]

            # Whitelist / Blacklist check
            if self.config.deny_tools and t_name in self.config.deny_tools:
                continue
            if self.config.allow_tools is not None and t_name not in self.config.allow_tools:
                continue

            tools.append(
                MCPTool(
                    server_id=self.config.server_id,
                    name=t_name,
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                )
            )
        return tools

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Executes a tool on the MCP server with output bounds enforcement."""
        params = {"name": tool_name, "arguments": arguments}
        resp = await self._send_request("tools/call", params, timeout=self.config.timeout_seconds)

        content_list = resp.get("content", [])
        text_parts = []
        for c in content_list:
            if isinstance(c, dict) and c.get("type") == "text":
                text_parts.append(c.get("text", ""))

        result_text = "\n".join(text_parts) if text_parts else json.dumps(resp)

        # Enforce output bounds limits
        if len(result_text.encode("utf-8")) > self.config.max_output_bytes:
            result_text = result_text[: self.config.max_output_bytes] + "\n... [TRUNCATED: MCP Output Exceeded Max Bounds]"

        if resp.get("isError"):
            raise MCPError(f"MCP tool '{tool_name}' returned error: {result_text}")

        return result_text

    async def list_resources(self) -> List[MCPResource]:
        """Lists resources available on the MCP server."""
        resp = await self._send_request("resources/list", {})
        res_raw = resp.get("resources", [])
        resources: List[MCPResource] = []
        for r in res_raw:
            if isinstance(r, dict) and "uri" in r:
                resources.append(
                    MCPResource(
                        server_id=self.config.server_id,
                        uri=r["uri"],
                        name=r.get("name", r["uri"]),
                        description=r.get("description", ""),
                        mime_type=r.get("mimeType"),
                    )
                )
        return resources

    async def read_resource(self, uri: str) -> str:
        """Reads contents of a resource from the MCP server."""
        resp = await self._send_request("resources/read", {"uri": uri})
        contents = resp.get("contents", [])
        text_parts = []
        for c in contents:
            if isinstance(c, dict) and "text" in c:
                text_parts.append(c["text"])
        return "\n".join(text_parts)

    async def list_prompts(self) -> List[MCPPrompt]:
        """Lists prompts available on the MCP server."""
        resp = await self._send_request("prompts/list", {})
        prompts_raw = resp.get("prompts", [])
        prompts: List[MCPPrompt] = []
        for p in prompts_raw:
            if isinstance(p, dict) and "name" in p:
                prompts.append(
                    MCPPrompt(
                        server_id=self.config.server_id,
                        name=p["name"],
                        description=p.get("description", ""),
                        arguments=p.get("arguments", []),
                    )
                )
        return prompts

    async def _send_request(self, method: str, params: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        self._request_id += 1
        req_id = self._request_id
        fut: asyncio.Future[Dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut

        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        try:
            await self.transport.send(payload)
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            raise MCPTimeoutError(f"MCP request '{method}' (id={req_id}) timed out after {timeout}s.")
        except Exception:
            self._pending_requests.pop(req_id, None)
            raise

    async def _read_loop(self) -> None:
        """Background loop reading JSON-RPC responses from the transport."""
        try:
            while True:
                msg = await self.transport.receive()
                if "id" in msg and msg["id"] in self._pending_requests:
                    fut = self._pending_requests.pop(msg["id"])
                    if not fut.done():
                        if "error" in msg:
                            err = msg["error"]
                            fut.set_exception(
                                MCPProtocolError(
                                    f"MCP server error ({err.get('code', 'unknown')}): {err.get('message', '')}"
                                )
                            )
                        else:
                            fut.set_result(msg.get("result", {}))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.state = MCPServerState.DEGRADED
            logger.debug("MCP read loop ended with error: %s", exc)
