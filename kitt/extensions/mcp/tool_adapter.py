"""Tool adapter bridging MCP tools into KITT's ToolRegistry, PolicyEngine, and ApprovalManager."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from kitt.extensions.mcp.client import MCPClient
from kitt.extensions.mcp.models import MCPTool


class MCPToolAdapter:
    """Bridges an MCP tool into a standard KITT tool handler."""

    def __init__(self, tool: MCPTool, client: MCPClient):
        self.tool = tool
        self.client = client

    @property
    def name(self) -> str:
        return self.tool.full_name

    @property
    def description(self) -> str:
        return f"[MCP: {self.tool.server_id}] {self.tool.description}"

    @property
    def schema(self) -> Dict[str, Any]:
        return dict(self.tool.input_schema)

    def execute(self, arguments: Dict[str, Any]) -> str:
        """Synchronous wrapper for ToolRegistry dispatch."""
        try:
            loop = asyncio.get_running_loop()
            # If inside running event loop, create task or execute in background
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.execute_async(arguments)).result(timeout=self.client.config.timeout_seconds)
        except RuntimeError:
            return asyncio.run(self.execute_async(arguments))

    async def execute_async(self, arguments: Dict[str, Any]) -> str:
        """Asynchronously dispatches the tool call to the MCP server."""
        return await self.client.call_tool(self.tool.name, arguments)
