"""Bridge MCP tools into KITT's synchronous ToolRegistry safely."""
from __future__ import annotations

from typing import Any, Dict

from kitt.extensions.mcp.client import MCPClient
from kitt.extensions.mcp.models import MCPTool


class MCPToolAdapter:
    def __init__(
        self,
        tool: MCPTool,
        client: MCPClient,
    ):
        self.tool = tool
        self.client = client

    @property
    def name(self) -> str:
        return self.tool.full_name

    @property
    def description(self) -> str:
        return (
            f"[MCP: {self.tool.server_id}] "
            f"{self.tool.description}"
        )

    @property
    def schema(self) -> Dict[str, Any]:
        return dict(self.tool.input_schema)

    def execute(
        self,
        arguments: Dict[str, Any],
    ) -> str:
        return self.client.call_tool_sync(
            self.tool.name,
            arguments,
        )

    async def execute_async(
        self,
        arguments: Dict[str, Any],
    ) -> str:
        return await self.client.call_tool(
            self.tool.name,
            arguments,
        )
