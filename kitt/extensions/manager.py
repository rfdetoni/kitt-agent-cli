"""ExtensionManager orchestrating plugins, hooks, and MCP servers."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from kitt.extensions.hooks.registry import HookRegistry
from kitt.extensions.mcp.manager import MCPManager
from kitt.extensions.plugins.loader import PluginLoader
from kitt.extensions.plugins.registry import PluginRegistry

logger = logging.getLogger("kitt.extensions.manager")


class ExtensionManager:
    """Composition root managing the lifecycle of plugins, hooks, and MCP servers."""

    def __init__(
        self,
        workspace_root: str = ".",
        event_bus=None,
        tool_registry=None,
        command_registry=None,
        hook_timeout: float = 5.0,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.event_bus = event_bus
        self.tool_registry = tool_registry
        self.command_registry = command_registry

        self.hooks = HookRegistry(default_timeout=hook_timeout)
        self.loader = PluginLoader(
            workspace_root=str(self.workspace_root),
            event_bus=event_bus,
            hook_registry=self.hooks,
            tool_registry=tool_registry,
            command_registry=command_registry,
        )
        self.plugins = PluginRegistry(loader=self.loader)
        self.mcp = MCPManager(
            workspace_root=str(self.workspace_root),
            tool_registry=tool_registry,
        )

    async def start(self) -> None:
        """Starts plugins, connects MCP servers, and triggers app.started hooks."""
        logger.debug("Starting ExtensionManager...")
        try:
            await self.plugins.start_all()
        except Exception as exc:
            logger.error("Error starting plugins: %s", exc)

        try:
            await self.mcp.connect_all_enabled()
        except Exception as exc:
            logger.error("Error connecting MCP servers: %s", exc)

        await self.hooks.run_observers("app.started", {"workspace_root": str(self.workspace_root)})

    async def stop(self) -> None:
        """Triggers app.stopping hooks, disconnects MCP, and unloads plugins."""
        logger.debug("Stopping ExtensionManager...")
        try:
            await self.hooks.run_observers("app.stopping", {"workspace_root": str(self.workspace_root)})
        except Exception as exc:
            logger.warning("Error running app.stopping hooks: %s", exc)

        try:
            await self.mcp.disconnect_all()
        except Exception as exc:
            logger.warning("Error disconnecting MCP servers: %s", exc)

        try:
            await self.plugins.stop_all()
        except Exception as exc:
            logger.warning("Error stopping plugins: %s", exc)

    def close(self) -> None:
        """Synchronous cleanup for ExtensionManager."""
        import asyncio
        import concurrent.futures
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(asyncio.run, self.stop()).result(timeout=5.0)
            else:
                loop.run_until_complete(self.stop())
        except RuntimeError:
            try:
                asyncio.run(self.stop())
            except Exception as exc:
                logger.warning("Error during ExtensionManager sync close: %s", exc)
