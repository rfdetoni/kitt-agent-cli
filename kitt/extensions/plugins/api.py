"""Scoped capability APIs exposed to plugins based on declared manifest permissions."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from kitt.extensions.errors import PluginPermissionError
from kitt.security.credentials import CredentialResolver


class PluginLogger:
    """Namespaced logger for plugins that redacts sensitive strings."""

    def __init__(self, plugin_name: str):
        self.logger = logging.getLogger(f"kitt.plugin.{plugin_name}")

    def debug(self, msg: str, *args: Any) -> None:
        self.logger.debug(CredentialResolver.redact_secrets(str(msg)), *args)

    def info(self, msg: str, *args: Any) -> None:
        self.logger.info(CredentialResolver.redact_secrets(str(msg)), *args)

    def warning(self, msg: str, *args: Any) -> None:
        self.logger.warning(CredentialResolver.redact_secrets(str(msg)), *args)

    def error(self, msg: str, *args: Any) -> None:
        self.logger.error(CredentialResolver.redact_secrets(str(msg)), *args)


class PluginConfigAPI:
    """Namespaced isolated configuration storage for a single plugin."""

    def __init__(self, plugin_name: str, config_dir: Optional[Path] = None):
        self.plugin_name = plugin_name
        self.config_dir = config_dir or (Path.home() / ".kitt" / "config" / "plugins")
        self.config_file = self.config_dir / f"{plugin_name}.json"
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.config_file.exists():
            try:
                self._data = json.loads(self.config_file.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def save(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{self.plugin_name}.", dir=str(self.config_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(self._data, indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.config_file)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class EventAPI:
    """Event observation API requiring 'events.read' permission."""

    def __init__(self, plugin_name: str, permissions: Set[str], event_bus=None):
        self.plugin_name = plugin_name
        self.permissions = permissions
        self.event_bus = event_bus

    def subscribe(self, event_name: str, handler: Callable[..., Any]) -> None:
        if "events.read" not in self.permissions:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' denied access to subscribe to events. Missing 'events.read' permission."
            )
        if self.event_bus and hasattr(self.event_bus, "subscribe"):
            self.event_bus.subscribe(event_name, handler)

    def publish(self, event_name: str, payload: Any) -> None:
        if "events.read" not in self.permissions:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' denied access to publish events. Missing 'events.read' permission."
            )
        if self.event_bus and hasattr(self.event_bus, "publish"):
            self.event_bus.publish(event_name, payload)


class HookAPI:
    """Lifecycle and interception hook registration API."""

    def __init__(
        self,
        plugin_name: str,
        permissions: Set[str],
        hook_registry=None,
        declared_hooks: tuple[str, ...] = (),
    ):
        self.plugin_name = plugin_name
        self.permissions = permissions
        self.hook_registry = hook_registry
        self.declared_hooks = frozenset(str(item) for item in declared_hooks)
        self.registered_hooks: List[Tuple[str, Callable[..., Any]]] = []

    def register(
        self,
        hook_name: str,
        handler: Callable[..., Any],
        *,
        priority: int = 0,
        fail_closed: bool = False,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        # Declared capabilities narrow exports; permissions remain authority.
        if self.declared_hooks and hook_name not in self.declared_hooks:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' hook '{hook_name}' is not declared "
                "in capabilities.hooks."
            )
        # Check permissions for modifying hooks
        if hook_name.startswith("tool.") and "tools.observe" not in self.permissions and "tools.modify" not in self.permissions:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' denied registering tool hook '{hook_name}'. Missing 'tools.observe' or 'tools.modify'."
            )
        if hook_name.startswith("model.") and "model.observe" not in self.permissions and "model.modify" not in self.permissions:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' denied registering model hook '{hook_name}'. Missing 'model.observe' or 'model.modify'."
            )
        if hook_name.startswith("context.") and "context.observe" not in self.permissions and "context.modify" not in self.permissions:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' denied registering context hook '{hook_name}'. Missing 'context.observe' or 'context.modify'."
            )
        if hook_name.startswith("memory.") and "memory.read" not in self.permissions and "memory.write" not in self.permissions:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' denied registering memory hook '{hook_name}'. Missing 'memory.read' or 'memory.write'."
            )

        if self.hook_registry:
            self.hook_registry.register(
                hook_name,
                handler,
                priority=priority,
                plugin_id=self.plugin_name,
                fail_closed=fail_closed,
                timeout_seconds=timeout_seconds,
            )
            self.registered_hooks.append((hook_name, handler))


class ToolAPI:
    """Tool registration API requiring 'tools.register' permission."""

    def __init__(
        self,
        plugin_name: str,
        permissions: Set[str],
        tool_registry=None,
        *,
        trusted_first_party: bool = False,
        declared_tools: tuple[str, ...] = (),
    ):
        self.plugin_name = plugin_name
        self.permissions = permissions
        self.tool_registry = tool_registry
        self.trusted_first_party = bool(trusted_first_party)
        self.declared_tools = frozenset(str(item) for item in declared_tools)
        self.registered_tool_names: List[str] = []

    def register(
        self,
        tool_name: str,
        handler: Callable[..., Any],
        description: str = "",
        schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        if "tools.register" not in self.permissions:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' denied tool registration. Missing 'tools.register' permission."
            )
        if self.declared_tools and tool_name not in self.declared_tools:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' tool '{tool_name}' is not declared "
                "in capabilities.tools."
            )
        if self.tool_registry and hasattr(self.tool_registry, "register"):
            self.tool_registry.register(
                tool_name,
                handler,
                description=description,
                schema=schema,
                owner_plugin_id=self.plugin_name,
                trusted_read_only=self.trusted_first_party,
            )
            self.registered_tool_names.append(tool_name)




class MCPAPI:
    """Scoped MCP capability for trusted plugin-managed server adapters."""

    def __init__(
        self,
        plugin_name: str,
        permissions: Set[str],
        mcp_manager=None,
    ):
        self.plugin_name = plugin_name
        self.permissions = permissions
        self.mcp_manager = mcp_manager
        self._owned_server_ids: Set[str] = set()

    def _require_manage(self) -> None:
        if "mcp.manage" not in self.permissions:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' denied MCP management. "
                "Missing 'mcp.manage' permission."
            )
        if self.mcp_manager is None:
            raise RuntimeError("MCP manager is unavailable in this runtime")

    @staticmethod
    def _safe_config(config, status: str | None = None) -> Dict[str, Any]:
        return {
            "server_id": config.server_id,
            "transport": config.transport,
            "url": config.url,
            "enabled": bool(config.enabled),
            "trust": config.trust,
            "source": config.source,
            "status": status,
        }

    def list_servers(self) -> List[Dict[str, Any]]:
        self._require_manage()
        rows: List[Dict[str, Any]] = []
        for config in self.mcp_manager.list_servers():
            state = self.mcp_manager.get_server_status(config.server_id)
            rows.append(self._safe_config(config, state.value))
        return rows

    def register_http(
        self,
        server_id: str,
        url: str,
        *,
        enabled: bool = True,
        trust: str = "restricted",
        allow_tools: Optional[List[str]] = None,
        deny_tools: Optional[List[str]] = None,
        timeout_seconds: float = 30.0,
        max_output_bytes: int = 2 * 1024 * 1024,
    ) -> Dict[str, Any]:
        self._require_manage()
        normalized = str(server_id or "").strip().lower()
        if not normalized:
            raise ValueError("MCP server_id is required")
        trust = str(trust or "restricted").strip().lower()
        if trust not in {"trusted", "restricted", "isolated"}:
            raise ValueError("MCP trust must be trusted, restricted, or isolated")

        for existing in self.mcp_manager.list_servers():
            if existing.server_id == normalized:
                return {
                    **self._safe_config(
                        existing,
                        self.mcp_manager.get_server_status(normalized).value,
                    ),
                    "registered": False,
                    "owned": False,
                }

        from kitt.extensions.mcp.models import MCPServerConfig

        config = MCPServerConfig(
            server_id=normalized,
            transport="http",
            url=str(url or "").strip(),
            enabled=bool(enabled),
            trust=trust,
            allow_tools=list(allow_tools) if allow_tools is not None else None,
            deny_tools=list(deny_tools or []),
            timeout_seconds=float(timeout_seconds),
            max_output_bytes=int(max_output_bytes),
            source=f"plugin:{self.plugin_name}",
        )
        self.mcp_manager.register_server(config)
        self._owned_server_ids.add(normalized)
        return {
            **self._safe_config(config, "DISCONNECTED"),
            "registered": True,
            "owned": True,
        }

    async def connect(self, server_id: str):
        self._require_manage()
        return await self.mcp_manager.connect(server_id)

    async def unregister(self, server_id: str) -> bool:
        self._require_manage()
        normalized = str(server_id or "").strip().lower()
        if normalized not in self._owned_server_ids:
            return False
        await self.mcp_manager.unregister_server(normalized)
        self._owned_server_ids.discard(normalized)
        return True

    async def unregister_owned(self) -> None:
        self._require_manage()
        for server_id in tuple(sorted(self._owned_server_ids, reverse=True)):
            await self.mcp_manager.unregister_server(server_id)
            self._owned_server_ids.discard(server_id)


class CommandAPI:
    """Slash command registration API requiring 'commands.register' permission."""

    def __init__(
        self,
        plugin_name: str,
        permissions: Set[str],
        command_registry=None,
        declared_commands: tuple[str, ...] = (),
    ):
        self.plugin_name = plugin_name
        self.permissions = permissions
        self.command_registry = command_registry
        self.declared_commands = frozenset(
            "/" + str(item).lstrip("/") for item in declared_commands
        )
        self.registered_commands: List[str] = []

    def register(self, command_name: str, handler: Callable[..., Any], help_text: str = "") -> None:
        if "commands.register" not in self.permissions:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' denied command registration. Missing 'commands.register' permission."
            )
        cmd = "/" + command_name.lstrip("/")
        if self.declared_commands and cmd not in self.declared_commands:
            raise PluginPermissionError(
                f"Plugin '{self.plugin_name}' command '{cmd}' is not declared "
                "in capabilities.commands."
            )
        if self.command_registry and hasattr(self.command_registry, "register"):
            self.command_registry.register(cmd, handler, help_text=help_text, owner_plugin_id=self.plugin_name)
            self.registered_commands.append(cmd)
