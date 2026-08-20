"""ExtensionManager orchestrating plugins, hooks, and MCP servers."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from kitt.extensions.errors import ExtensionStartupFailed
from kitt.extensions.hooks.registry import HookRegistry
from kitt.extensions.mcp.manager import MCPManager
from kitt.extensions.plugins.loader import PluginLoader
from kitt.extensions.plugins.registry import PluginRegistry
from kitt.extensions.plugins.security import PluginStateStore, PluginTrustStore

logger = logging.getLogger("kitt.extensions.manager")


class ExtensionManager:
    STATE_STOPPED = "STOPPED"
    STATE_STARTING = "STARTING"
    STATE_STARTED = "STARTED"
    STATE_STOPPING = "STOPPING"

    """Composition root managing the lifecycle of plugins, hooks, and MCP servers."""

    def __init__(
        self,
        workspace_root: str = ".",
        event_bus=None,
        tool_registry=None,
        command_registry=None,
        hook_timeout: float = 5.0,
        plugin_trust_path: Optional[str] = None,
        plugin_state_path: Optional[str] = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.event_bus = event_bus
        self.tool_registry = tool_registry
        self.command_registry = command_registry

        self.hooks = HookRegistry(default_timeout=hook_timeout)
        self.plugin_trust = PluginTrustStore(self.workspace_root, path=plugin_trust_path)
        self.plugin_state = PluginStateStore(self.workspace_root, path=plugin_state_path)
        self.loader = PluginLoader(
            workspace_root=str(self.workspace_root),
            event_bus=event_bus,
            hook_registry=self.hooks,
            tool_registry=tool_registry,
            command_registry=command_registry,
            trust_store=self.plugin_trust,
        )
        self.plugins = PluginRegistry(loader=self.loader, state_store=self.plugin_state)
        self._started = False
        self.state = self.STATE_STOPPED
        self.mcp = MCPManager(
            workspace_root=str(self.workspace_root),
            tool_registry=tool_registry,
        )

    async def start(self) -> None:
        """Starts plugins, connects MCP servers, and triggers app.started hooks."""
        if self.state == self.STATE_STARTED:
            return
        if self.state == self.STATE_STARTING:
            raise RuntimeError("ExtensionManager.start() already in progress")
        logger.debug("Starting ExtensionManager...")
        self.state = self.STATE_STARTING
        try:
            await self.plugins.start_all()
            await self.mcp.connect_all_enabled()
            await self.hooks.run_observers(
                "app.started",
                {"workspace_root": str(self.workspace_root)},
            )
        except Exception as exc:
            if self.event_bus is not None:
                self.event_bus.publish(
                    "ExtensionStartupFailed",
                    {"error": str(exc), "workspace_root": str(self.workspace_root)},
                )
            try:
                await self.stop()
            except Exception:
                logger.exception("Extension rollback failed after startup error")
            raise ExtensionStartupFailed(str(exc)) from exc
        self._started = True
        self.state = self.STATE_STARTED

    async def stop(self) -> None:
        """Triggers app.stopping hooks, disconnects MCP, and unloads plugins."""
        if self.state == self.STATE_STOPPED:
            return
        self.state = self.STATE_STOPPING
        logger.debug("Stopping ExtensionManager...")
        try:
            await self.hooks.run_observers(
                "app.stopping", {"workspace_root": str(self.workspace_root)}
            )
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
        finally:
            self._started = False
            self.state = self.STATE_STOPPED

    def close(self) -> None:
        """Synchronous cleanup for ExtensionManager."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            raise RuntimeError(
                "ExtensionManager.close() cannot run inside an active event loop; await stop()."
            )
        asyncio.run(self.stop())
