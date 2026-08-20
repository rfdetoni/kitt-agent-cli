"""Plugin registry coordinating discovery, lifecycle, and activation."""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from kitt.extensions.errors import PluginLoadError
from kitt.extensions.models import PluginManifest, PluginState
from kitt.extensions.plugins.loader import PluginInstance, PluginLoader
from kitt.extensions.plugins.security import PluginStateStore

logger = logging.getLogger("kitt.extensions.plugins.registry")


class PluginRegistry:
    def __init__(
        self,
        loader: Optional[PluginLoader] = None,
        state_store: Optional[PluginStateStore] = None,
    ):
        self.loader = loader or PluginLoader()
        self.state_store = state_store or PluginStateStore(self.loader.workspace_root)
        self._lock = threading.RLock()
        self._manifests: Dict[str, PluginManifest] = {}
        self._plugins: Dict[str, PluginInstance] = {}
        enabled, disabled = self.state_store.load()
        self._explicit_enabled: set[str] = set(enabled)
        self._disabled_plugins: set[str] = set(disabled)

    def discover(self) -> Dict[str, PluginManifest]:
        manifests = self.loader.discover_manifests()
        with self._lock:
            self._manifests = manifests
            return dict(self._manifests)

    def _manifest(self, name: str) -> PluginManifest:
        plugin_id = name.strip().lower()
        with self._lock:
            manifest = self._manifests.get(plugin_id)
        if manifest is None:
            self.discover()
            with self._lock:
                manifest = self._manifests.get(plugin_id)
        if manifest is None:
            raise PluginLoadError(
                f"Plugin '{plugin_id}' not found in discovery directories."
            )
        with self._lock:
            if plugin_id in self._disabled_plugins:
                raise PluginLoadError(
                    f"Plugin '{plugin_id}' is currently disabled."
                )
        return manifest

    def load(self, name: str) -> PluginInstance:
        plugin_id = name.strip().lower()
        instance = self.loader.load(self._manifest(plugin_id))
        with self._lock:
            self._plugins[plugin_id] = instance
        return instance

    async def load_async(self, name: str) -> PluginInstance:
        plugin_id = name.strip().lower()
        instance = await self.loader.load_async(self._manifest(plugin_id))
        with self._lock:
            self._plugins[plugin_id] = instance
        return instance

    async def start(self, name: str) -> None:
        plugin_id = name.strip().lower()
        instance = self.get(plugin_id)
        if instance is None:
            instance = await self.load_async(plugin_id)
        if instance.handle:
            await instance.handle.start()
        instance.state = PluginState.ACTIVE

    async def unload(self, name: str) -> None:
        plugin_id = name.strip().lower()
        with self._lock:
            instance = self._plugins.get(plugin_id)
        if not instance:
            return

        instance.state = PluginState.STOPPED
        if instance.handle:
            try:
                await instance.handle.stop()
            except Exception as exc:
                logger.warning("Error stopping plugin '%s': %s", plugin_id, exc)

        if self.loader.hook_registry:
            self.loader.hook_registry.unregister(plugin_id=plugin_id)
        if self.loader.tool_registry and hasattr(
            self.loader.tool_registry, "unregister_by_owner"
        ):
            self.loader.tool_registry.unregister_by_owner(plugin_id)

        with self._lock:
            self._plugins.pop(plugin_id, None)

    def disable(self, name: str) -> None:
        plugin_id = name.strip().lower()
        with self._lock:
            self._disabled_plugins.add(plugin_id)
            self._explicit_enabled.discard(plugin_id)
            if plugin_id in self._plugins:
                self._plugins[plugin_id].state = PluginState.DISABLED
        self.state_store.set_enabled(plugin_id, False)

    def enable(self, name: str) -> None:
        plugin_id = name.strip().lower()
        with self._lock:
            self._disabled_plugins.discard(plugin_id)
            self._explicit_enabled.add(plugin_id)
            if plugin_id in self._plugins:
                self._plugins[plugin_id].state = PluginState.LOADED
        self.state_store.set_enabled(plugin_id, True)

    def is_enabled(self, name: str, manifest: Optional[PluginManifest] = None) -> bool:
        plugin_id = name.strip().lower()
        with self._lock:
            if plugin_id in self._disabled_plugins:
                return False
            if plugin_id in self._explicit_enabled:
                return True
        if manifest is None:
            manifest = self._manifests.get(plugin_id)
        return bool(manifest and manifest.enabled_by_default)

    def get(self, name: str) -> Optional[PluginInstance]:
        with self._lock:
            return self._plugins.get(name.strip().lower())

    def list(self) -> List[PluginInstance]:
        with self._lock:
            return list(self._plugins.values())

    def list_manifests(self) -> List[PluginManifest]:
        with self._lock:
            return list(self._manifests.values())

    async def start_all(self) -> None:
        manifests = self.discover()
        for plugin_id, manifest in manifests.items():
            if not self.is_enabled(plugin_id, manifest):
                continue
            try:
                instance = await self.load_async(plugin_id)
                if instance.handle:
                    await instance.handle.start()
                instance.state = PluginState.ACTIVE
            except Exception as exc:
                logger.error("Failed to start plugin '%s': %s", plugin_id, exc)
                if manifest.is_critical:
                    raise

    async def reload(self, name: str) -> PluginInstance:
        plugin_id = name.strip().lower()
        await self.unload(plugin_id)
        await self.start(plugin_id)
        instance = self.get(plugin_id)
        if instance is None:
            raise PluginLoadError(f"Plugin '{plugin_id}' failed to reload")
        return instance

    async def stop_all(self) -> None:
        with self._lock:
            plugin_ids = list(self._plugins)
        for plugin_id in plugin_ids:
            try:
                await self.unload(plugin_id)
            except Exception as exc:
                logger.error("Error stopping plugin '%s': %s", plugin_id, exc)
