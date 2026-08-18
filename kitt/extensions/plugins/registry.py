"""Plugin registry coordinating plugin lifecycle, states, and runtime activation."""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from kitt.extensions.errors import PluginLoadError
from kitt.extensions.models import PluginManifest, PluginState
from kitt.extensions.plugins.loader import PluginInstance, PluginLoader

logger = logging.getLogger("kitt.extensions.plugins.registry")


class PluginRegistry:
    """Central registry managing loaded plugins, states, and lifecycle transitions."""

    def __init__(self, loader: Optional[PluginLoader] = None):
        self.loader = loader or PluginLoader()
        self._lock = threading.RLock()
        self._manifests: Dict[str, PluginManifest] = {}
        self._plugins: Dict[str, PluginInstance] = {}
        self._disabled_plugins: set[str] = set()

    def discover(self) -> Dict[str, PluginManifest]:
        with self._lock:
            self._manifests = self.loader.discover_manifests()
            return dict(self._manifests)

    def load(self, name: str) -> PluginInstance:
        pid = name.strip().lower()
        with self._lock:
            if pid not in self._manifests:
                self.discover()
            manifest = self._manifests.get(pid)
            if not manifest:
                raise PluginLoadError(f"Plugin '{pid}' not found in discovery directories.")

            if pid in self._disabled_plugins:
                raise PluginLoadError(f"Plugin '{pid}' is currently disabled.")

            instance = self.loader.load(manifest)
            self._plugins[pid] = instance
            return instance

    async def start(self, name: str) -> None:
        pid = name.strip().lower()
        instance = self.get(pid)
        if not instance:
            instance = self.load(pid)
        if instance.handle:
            await instance.handle.start()
        instance.state = PluginState.ACTIVE

    async def unload(self, name: str) -> None:
        pid = name.strip().lower()
        with self._lock:
            instance = self._plugins.get(pid)
            if not instance:
                return
            instance.state = PluginState.STOPPED
            if instance.handle:
                try:
                    await instance.handle.stop()
                except Exception as exc:
                    logger.warning("Error stopping plugin '%s': %s", pid, exc)

            # Unregister hooks and tools
            if self.loader.hook_registry:
                self.loader.hook_registry.unregister(plugin_id=pid)
            if self.loader.tool_registry and hasattr(self.loader.tool_registry, "unregister_by_owner"):
                self.loader.tool_registry.unregister_by_owner(pid)

            del self._plugins[pid]

    def disable(self, name: str) -> None:
        pid = name.strip().lower()
        with self._lock:
            self._disabled_plugins.add(pid)
            if pid in self._plugins:
                self._plugins[pid].state = PluginState.DISABLED

    def enable(self, name: str) -> None:
        pid = name.strip().lower()
        with self._lock:
            self._disabled_plugins.discard(pid)
            if pid in self._plugins:
                self._plugins[pid].state = PluginState.LOADED

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
        with self._lock:
            self.discover()
            for pid, manifest in self._manifests.items():
                if manifest.enabled_by_default and pid not in self._disabled_plugins:
                    try:
                        instance = self.load(pid)
                        if instance.handle:
                            await instance.handle.start()
                        instance.state = PluginState.ACTIVE
                    except Exception as exc:
                        logger.error("Failed to start plugin '%s': %s", pid, exc)
                        if manifest.is_critical:
                            raise

    async def stop_all(self) -> None:
        with self._lock:
            for pid in list(self._plugins.keys()):
                try:
                    await self.unload(pid)
                except Exception as exc:
                    logger.error("Error stopping plugin '%s': %s", pid, exc)
