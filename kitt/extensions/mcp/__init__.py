"""Model Context Protocol (MCP) subsystem package."""
from kitt.extensions.mcp.client import MCPClient
from kitt.extensions.mcp.manager import MCPManager
from kitt.extensions.mcp.models import (
    MCPPrompt,
    MCPResource,
    MCPServerConfig,
    MCPServerState,
    MCPTool,
)
from kitt.extensions.mcp.tool_adapter import MCPToolAdapter
from kitt.extensions.mcp.transport import (
    HTTPTransport,
    InProcessTransport,
    MCPTransport,
    StdioTransport,
)

__all__ = [
    "MCPClient",
    "MCPManager",
    "MCPServerConfig",
    "MCPServerState",
    "MCPTool",
    "MCPResource",
    "MCPPrompt",
    "MCPToolAdapter",
    "MCPTransport",
    "StdioTransport",
    "HTTPTransport",
    "InProcessTransport",
]
