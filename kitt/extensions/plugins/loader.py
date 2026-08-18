"""Plugin loader, discovery, dynamic module importing, and lifecycle handles."""
from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from kitt.extensions.errors import PluginLoadError
from kitt.extensions.manifest import parse_manifest_file
from kitt.extensions.models import PluginIdentity, PluginManifest, PluginState
from kitt.extensions.plugins.api import (
    CommandAPI,
    EventAPI,
    HookAPI,
    PluginConfigAPI,
    PluginLogger,
    ToolAPI,
)
from kitt.extensions.plugins.context import PluginContext

logger = logging.getLogger("kitt.extensions.plugins.loader")


class PluginHandle(Protocol):
    """Protocol for active plugin lifecycle handles."""

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...


class DefaultPluginHandle:
    """Default no-op plugin handle for simple setup functions."""

    def __init__(self, start_fn: Optional[Callable] = None, stop_fn: Optional[Callable] = None):
        self._start_fn = start_fn
        self._stop_fn = stop_fn

    async def start(self) -> None:
        if self._start_fn:
            res = self._start_fn()
            if inspect.iscoroutine(res):
                await res

    async def stop(self) -> None:
        if self._stop_fn:
            res = self._stop_fn()
            if inspect.iscoroutine(res):
                await res


@dataclass
class PluginInstance:
    manifest: PluginManifest
    identity: PluginIdentity
    context: PluginContext
    handle: Optional[PluginHandle] = None
    state: PluginState = PluginState.DISCOVERED
    last_error: Optional[str] = None


class PluginLoader:
    """Discovers, validates, loads, and manages plugin modules with transactional rollback."""

    def __init__(
        self,
        workspace_root: str = ".",
        global_plugins_dir: Optional[str] = None,
        event_bus=None,
        hook_registry=None,
        tool_registry=None,
        command_registry=None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.global_plugins_dir = Path(global_plugins_dir or (Path.home() / ".kitt" / "plugins")).resolve()
        self.event_bus = event_bus
        self.hook_registry = hook_registry
        self.tool_registry = tool_registry
        self.command_registry = command_registry

    def discover_manifests(self) -> Dict[str, PluginManifest]:
        """Discovers plugins in workspace and global directories. Workspace overrides global."""
        manifests: Dict[str, PluginManifest] = {}

        # 1. Discover global plugins (~/.kitt/plugins)
        if self.global_plugins_dir.is_dir():
            for child in sorted(self.global_plugins_dir.iterdir()):
                if child.is_dir():
                    manifest_file = child / "plugin.toml"
                    if manifest_file.is_file():
                        try:
                            m = parse_manifest_file(manifest_file, source="global")
                            manifests[m.name] = m
                        except Exception as exc:
                            logger.warning("Failed to parse global plugin manifest in %s: %s", child, exc)

        # 2. Discover workspace plugins (<workspace>/.kitt/plugins)
        ws_plugins_dir = self.workspace_root / ".kitt" / "plugins"
        if ws_plugins_dir.is_dir():
            for child in sorted(ws_plugins_dir.iterdir()):
                if child.is_dir():
                    manifest_file = child / "plugin.toml"
                    if manifest_file.is_file():
                        try:
                            m = parse_manifest_file(manifest_file, source="workspace")
                            if m.name in manifests:
                                logger.info("Workspace plugin '%s' overrides global plugin.", m.name)
                            manifests[m.name] = m
                        except Exception as exc:
                            logger.warning("Failed to parse workspace plugin manifest in %s: %s", child, exc)

        return manifests

    def load(self, manifest: PluginManifest) -> PluginInstance:
        """Loads a plugin module and invokes setup(ctx) with transactional rollback on failure."""
        manifest_dir = manifest.manifest_path.parent if manifest.manifest_path else self.workspace_root
        identity = PluginIdentity(
            name=manifest.name,
            version=manifest.version,
            source=manifest.source,
            root_path=manifest_dir,
        )

        events_api = EventAPI(manifest.name, manifest.permissions, self.event_bus)
        hooks_api = HookAPI(manifest.name, manifest.permissions, self.hook_registry)
        tools_api = ToolAPI(manifest.name, manifest.permissions, self.tool_registry)
        commands_api = CommandAPI(manifest.name, manifest.permissions, self.command_registry)
        config_api = PluginConfigAPI(manifest.name)
        plugin_logger = PluginLogger(manifest.name)

        ctx = PluginContext(
            identity=identity,
            manifest=manifest,
            events=events_api,
            hooks=hooks_api,
            tools=tools_api,
            commands=commands_api,
            config=config_api,
            logger=plugin_logger,
        )

        instance = PluginInstance(
            manifest=manifest,
            identity=identity,
            context=ctx,
            state=PluginState.LOADING,
        )

        try:
            # Parse entrypoint format: "module_file:setup_function" (e.g. "plugin:setup" or "main:init")
            parts = manifest.entrypoint.split(":", 1)
            module_name_rel = parts[0]
            func_name = parts[1] if len(parts) > 1 else "setup"

            module_file = manifest_dir / f"{module_name_rel}.py"
            if not module_file.is_file():
                # Check direct filename
                module_file = manifest_dir / module_name_rel
                if not module_file.is_file():
                    raise PluginLoadError(f"Plugin '{manifest.name}' entrypoint file not found: {module_file}")

            # Dynamic import
            spec = importlib.util.spec_from_file_location(f"kitt_plugin_{manifest.name}", module_file)
            if spec is None or spec.loader is None:
                raise PluginLoadError(f"Failed to create module spec for plugin '{manifest.name}' at {module_file}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[f"kitt_plugin_{manifest.name}"] = module
            spec.loader.exec_module(module)

            setup_callable = getattr(module, func_name, None)
            if not callable(setup_callable):
                raise PluginLoadError(f"Plugin '{manifest.name}' entrypoint '{func_name}' is not callable in {module_file}")

            # Invoke setup(ctx)
            result = setup_callable(ctx)
            if inspect.iscoroutine(result):
                # Note: async setup can be awaited or wrapped
                pass

            handle: Optional[PluginHandle] = None
            if hasattr(result, "start") and hasattr(result, "stop"):
                handle = result
            elif isinstance(result, tuple) and len(result) == 2:
                handle = DefaultPluginHandle(start_fn=result[0], stop_fn=result[1])
            else:
                handle = DefaultPluginHandle()

            instance.handle = handle
            instance.state = PluginState.LOADED
            return instance

        except Exception as exc:
            instance.state = PluginState.FAILED
            instance.last_error = str(exc)
            # Transactional Rollback: unregister hooks and tools registered during partial setup
            if self.hook_registry:
                self.hook_registry.unregister(plugin_id=manifest.name)
            if self.tool_registry and hasattr(self.tool_registry, "unregister_by_owner"):
                self.tool_registry.unregister_by_owner(manifest.name)
            raise PluginLoadError(f"Failed to load plugin '{manifest.name}': {exc}") from exc
