"""ExtensionManager orchestrating plugins, hooks, and MCP servers."""
from __future__ import annotations

import asyncio
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
    """Transactional composition root for plugins, hooks and MCP."""

    STATE_STOPPED = "STOPPED"
    STATE_STARTING = "STARTING"
    STATE_STARTED = "STARTED"
    STATE_STOPPING = "STOPPING"

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
        self.plugin_trust = PluginTrustStore(
            self.workspace_root, path=plugin_trust_path
        )
        self.plugin_state = PluginStateStore(
            self.workspace_root, path=plugin_state_path
        )
        self.loader = PluginLoader(
            workspace_root=str(self.workspace_root),
            event_bus=event_bus,
            hook_registry=self.hooks,
            tool_registry=tool_registry,
            command_registry=command_registry,
            trust_store=self.plugin_trust,
        )
        self.plugins = PluginRegistry(
            loader=self.loader,
            state_store=self.plugin_state,
        )
        self.mcp = MCPManager(
            workspace_root=str(self.workspace_root),
            tool_registry=tool_registry,
        )
        self._started = False
        self.state = self.STATE_STOPPED
        self._lifecycle_lock: Optional[asyncio.Lock] = None

    def _lock(self) -> asyncio.Lock:
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        return self._lifecycle_lock

    def _publish(self, event_name: str, payload: dict) -> None:
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish(event_name, payload)
        except Exception:
            logger.debug(
                "Failed publishing extension lifecycle event",
                exc_info=True,
            )

    async def _cleanup(self, *, run_stopping_hook: bool) -> list[str]:
        errors: list[str] = []
        if run_stopping_hook:
            try:
                await self.hooks.run_observers(
                    "app.stopping",
                    {"workspace_root": str(self.workspace_root)},
                )
            except Exception as exc:
                errors.append(f"app.stopping: {exc}")

        try:
            await self.mcp.disconnect_all()
        except Exception as exc:
            errors.append(f"mcp: {exc}")

        try:
            await self.plugins.stop_all()
        except Exception as exc:
            errors.append(f"plugins: {exc}")
        return errors

    async def start(self) -> None:
        """Start atomically. Critical plugin/hook failures roll everything back."""
        async with self._lock():
            if self.state == self.STATE_STARTED:
                return
            if self.state != self.STATE_STOPPED:
                raise RuntimeError(
                    f"ExtensionManager cannot start from state {self.state}"
                )

            self.state = self.STATE_STARTING
            logger.debug("Starting ExtensionManager...")
            try:
                await self.plugins.start_all()
                await self.mcp.connect_all_enabled()
                await self.hooks.run_observers(
                    "app.started",
                    {"workspace_root": str(self.workspace_root)},
                )
            except Exception as exc:
                rollback_errors = await self._cleanup(
                    run_stopping_hook=False
                )
                self._started = False
                self.state = self.STATE_STOPPED
                self._publish(
                    "ExtensionStartupFailed",
                    {
                        "error": str(exc),
                        "workspace_root": str(self.workspace_root),
                    },
                )
                message = str(exc)
                if rollback_errors:
                    message += "; rollback errors: " + "; ".join(
                        rollback_errors
                    )
                raise ExtensionStartupFailed(message) from exc

            self._started = True
            self.state = self.STATE_STARTED

    async def stop(self) -> None:
        """Stop exactly once and surface cleanup failures to the runtime."""
        async with self._lock():
            if self.state == self.STATE_STOPPED:
                return
            if self.state != self.STATE_STARTED:
                raise RuntimeError(
                    f"ExtensionManager cannot stop from state {self.state}"
                )

            self.state = self.STATE_STOPPING
            errors = await self._cleanup(run_stopping_hook=True)
            self._started = False
            self.state = self.STATE_STOPPED
            if errors:
                raise RuntimeError(
                    "Extension shutdown errors: " + "; ".join(errors)
                )

    def close(self) -> None:
        """Synchronous compatibility wrapper; async callers must await stop()."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            raise RuntimeError(
                "ExtensionManager.close() cannot run inside an active event "
                "loop; await stop()."
            )
        asyncio.run(self.stop())
