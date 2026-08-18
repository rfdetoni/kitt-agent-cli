"""MCP Manager coordinating multi-server connections, tools syncing, and lifecycle."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from kitt.extensions.errors import MCPError
from kitt.extensions.mcp.client import MCPClient
from kitt.extensions.mcp.models import (
    MCPPrompt,
    MCPResource,
    MCPServerConfig,
    MCPServerState,
    MCPTool,
)
from kitt.extensions.mcp.tool_adapter import MCPToolAdapter
from kitt.extensions.mcp.transport import MCPTransport, StdioTransport

logger = logging.getLogger("kitt.extensions.mcp.manager")


class MCPManager:
    """Central manager for Model Context Protocol servers and integrated capabilities."""

    def __init__(
        self,
        workspace_root: str = ".",
        config_file: Optional[str] = None,
        tool_registry=None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.config_file = Path(config_file or (Path.home() / ".kitt" / "config" / "mcp.json")).resolve()
        self.tool_registry = tool_registry
        self._lock = threading.RLock()
        self._configs: Dict[str, MCPServerConfig] = {}
        self._clients: Dict[str, MCPClient] = {}
        self._server_tools: Dict[str, List[MCPTool]] = {}
        self._load_configs()

    def _load_configs(self) -> None:
        """Loads MCP configurations from global and workspace configs."""
        configs_data: Dict[str, Any] = {}

        # 1. Global config (~/.kitt/config/mcp.json)
        if self.config_file.is_file():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                configs_data.update(data.get("mcp", data))
            except Exception as exc:
                logger.warning("Failed to load global MCP config from %s: %s", self.config_file, exc)

        # 2. Workspace config (<workspace>/.kitt/mcp.json)
        ws_mcp_file = self.workspace_root / ".kitt" / "mcp.json"
        if ws_mcp_file.is_file():
            try:
                ws_data = json.loads(ws_mcp_file.read_text(encoding="utf-8"))
                configs_data.update(ws_data.get("mcp", ws_data))
            except Exception as exc:
                logger.warning("Failed to load workspace MCP config from %s: %s", ws_mcp_file, exc)

        for s_id, s_data in configs_data.items():
            if isinstance(s_data, dict):
                self._configs[s_id] = MCPServerConfig(
                    server_id=s_id,
                    transport=s_data.get("transport", "stdio"),
                    command=s_data.get("command"),
                    args=s_data.get("args", []),
                    env=s_data.get("env", {}),
                    url=s_data.get("url"),
                    enabled=bool(s_data.get("enabled", True)),
                    trust=s_data.get("trust", "restricted"),
                    allow_tools=s_data.get("allow_tools"),
                    deny_tools=s_data.get("deny_tools", []),
                    timeout_seconds=float(s_data.get("timeout_seconds", 30.0)),
                )

    def register_server(self, config: MCPServerConfig, custom_transport: Optional[MCPTransport] = None) -> None:
        with self._lock:
            self._configs[config.server_id] = config
            if custom_transport:
                self._clients[config.server_id] = MCPClient(config, custom_transport)

    async def connect(self, server_id: str, transport: Optional[MCPTransport] = None) -> MCPClient:
        """Connects to an MCP server, performs handshake, and syncs exposed tools."""
        s_id = server_id.strip().lower()
        with self._lock:
            config = self._configs.get(s_id)
            if not config:
                raise MCPError(f"MCP server '{s_id}' not found in configuration.")

            client = self._clients.get(s_id)
            if not client:
                t = transport or StdioTransport(command=config.command or "", args=config.args, env=config.env)
                client = MCPClient(config, t)
                self._clients[s_id] = client

        await client.connect()

        # Discover and register tools
        try:
            tools = await client.list_tools()
            with self._lock:
                self._server_tools[s_id] = tools

            # Register with KITT's tool registry
            if self.tool_registry and hasattr(self.tool_registry, "register"):
                for tool in tools:
                    adapter = MCPToolAdapter(tool, client)
                    self.tool_registry.register(
                        tool.full_name,
                        adapter.execute,
                        description=adapter.description,
                        schema=adapter.schema,
                        owner_plugin_id=f"mcp:{s_id}",
                    )
        except Exception as exc:
            logger.error("Failed to list tools from MCP server '%s': %s", s_id, exc)

        return client

    async def disconnect(self, server_id: str) -> None:
        s_id = server_id.strip().lower()
        with self._lock:
            client = self._clients.get(s_id)
            if not client:
                return

            # Unregister tools from ToolRegistry
            if self.tool_registry and hasattr(self.tool_registry, "unregister_by_owner"):
                self.tool_registry.unregister_by_owner(f"mcp:{s_id}")

            self._server_tools.pop(s_id, None)

        await client.disconnect()

    async def reconnect(self, server_id: str) -> MCPClient:
        await self.disconnect(server_id)
        return await self.connect(server_id)

    def list_servers(self) -> List[MCPServerConfig]:
        with self._lock:
            return list(self._configs.values())

    def get_server_status(self, server_id: str) -> MCPServerState:
        with self._lock:
            client = self._clients.get(server_id.strip().lower())
            return client.state if client else MCPServerState.DISCONNECTED

    def list_tools(self, server_id: Optional[str] = None) -> List[MCPTool]:
        with self._lock:
            if server_id:
                return list(self._server_tools.get(server_id.strip().lower(), []))
            all_tools = []
            for t_list in self._server_tools.values():
                all_tools.extend(t_list)
            return all_tools

    async def connect_all_enabled(self) -> None:
        for s_id, cfg in list(self._configs.items()):
            if cfg.enabled and cfg.command:
                try:
                    await self.connect(s_id)
                except Exception as exc:
                    logger.warning("Failed to connect enabled MCP server '%s': %s", s_id, exc)

    async def disconnect_all(self) -> None:
        for s_id in list(self._clients.keys()):
            try:
                await self.disconnect(s_id)
            except Exception as exc:
                logger.warning("Error disconnecting MCP server '%s': %s", s_id, exc)
