"""Plugin registry coordinating discovery, lifecycle and activation."""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from kitt.extensions.errors import PluginLoadError
from kitt.extensions.models import PluginManifest, PluginState
from kitt.extensions.plugins.loader import (
    PluginInstance,
    PluginLoader,
)
from kitt.extensions.plugins.security import PluginStateStore

logger = logging.getLogger("kitt.extensions.plugins.registry")


class PluginRegistry:
    def __init__(
        self,
        loader: Optional[PluginLoader] = None,
        state_store: Optional[PluginStateStore] = None,
    ):
        self.loader = loader or PluginLoader()
        self.state_store = state_store or PluginStateStore(
            self.loader.workspace_root
        )
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
                f"Plugin '{plugin_id}' not found"
            )
        with self._lock:
            if plugin_id in self._disabled_plugins:
                raise PluginLoadError(
                    f"Plugin '{plugin_id}' is disabled"
                )
        return manifest

    def load(self, name: str) -> PluginInstance:
        plugin_id = name.strip().lower()
        instance = self.loader.load(
            self._manifest(plugin_id)
        )
        with self._lock:
            self._plugins[plugin_id] = instance
        return instance

    async def load_async(
        self, name: str
    ) -> PluginInstance:
        plugin_id = name.strip().lower()
        instance = await self.loader.load_async(
            self._manifest(plugin_id)
        )
        with self._lock:
            self._plugins[plugin_id] = instance
        return instance

    async def start(self, name: str) -> None:
        plugin_id = name.strip().lower()
        instance = self.get(plugin_id)
        if instance is None:
            instance = await self.load_async(plugin_id)
        try:
            if instance.handle:
                await instance.handle.start()
            instance.state = PluginState.ACTIVE
        except Exception as start_error:
            instance.state = PluginState.FAILED
            try:
                await self.unload(plugin_id)
            except Exception:
                logger.exception(
                    "Plugin '%s' cleanup failed after start error",
                    plugin_id,
                )
            raise start_error

    async def unload(self, name: str) -> None:
        plugin_id = name.strip().lower()
        with self._lock:
            instance = self._plugins.get(plugin_id)
        if not instance:
            return

        stop_error: Optional[Exception] = None
        if instance.handle:
            try:
                await instance.handle.stop()
            except Exception as exc:
                stop_error = exc
                logger.warning(
                    "Error stopping plugin '%s': %s",
                    plugin_id,
                    exc,
                )

        if self.loader.hook_registry:
            self.loader.hook_registry.unregister(
                plugin_id=plugin_id
            )
        if (
            self.loader.tool_registry
            and hasattr(
                self.loader.tool_registry,
                "unregister_by_owner",
            )
        ):
            self.loader.tool_registry.unregister_by_owner(
                plugin_id
            )
        self.loader.unload_instance(instance)
        instance.state = PluginState.STOPPED

        with self._lock:
            self._plugins.pop(plugin_id, None)

        if stop_error is not None:
            raise RuntimeError(
                f"Plugin '{plugin_id}' stop failed: {stop_error}"
            ) from stop_error

    def disable(self, name: str) -> None:
        plugin_id = name.strip().lower()
        with self._lock:
            self._disabled_plugins.add(plugin_id)
            self._explicit_enabled.discard(plugin_id)
        self.state_store.set_enabled(
            plugin_id, False
        )

    def enable(self, name: str) -> None:
        plugin_id = name.strip().lower()
        with self._lock:
            self._disabled_plugins.discard(plugin_id)
            self._explicit_enabled.add(plugin_id)
        self.state_store.set_enabled(
            plugin_id, True
        )

    def is_enabled(
        self,
        name: str,
        manifest: Optional[PluginManifest] = None,
    ) -> bool:
        plugin_id = name.strip().lower()
        with self._lock:
            if plugin_id in self._disabled_plugins:
                return False
            if plugin_id in self._explicit_enabled:
                return True
        if manifest is None:
            manifest = self._manifests.get(plugin_id)
        return bool(
            manifest and manifest.enabled_by_default
        )

    def get(
        self, name: str
    ) -> Optional[PluginInstance]:
        with self._lock:
            return self._plugins.get(
                name.strip().lower()
            )

    def list(self) -> List[PluginInstance]:
        with self._lock:
            return list(self._plugins.values())

    def list_manifests(self) -> List[PluginManifest]:
        with self._lock:
            return list(self._manifests.values())

    async def start_all(self) -> None:
        manifests = self.discover()
        for plugin_id, manifest in manifests.items():
            # Manifest flags are plugin-controlled input. Autostart and
            # critical startup semantics are honored only after local trust
            # verification for the exact manifest content.
            try:
                trusted = self.loader.trust_store.is_trusted(
                    manifest
                )
            except Exception as exc:
                logger.warning(
                    "Skipping plugin '%s': trust verification failed: %s",
                    plugin_id,
                    exc,
                )
                trusted = False
            if not trusted:
                logger.info(
                    "Skipping untrusted plugin '%s' during autostart.",
                    plugin_id,
                )
                continue
            if not self.is_enabled(
                plugin_id, manifest
            ):
                continue
            try:
                await self.start(plugin_id)
            except Exception as exc:
                logger.error(
                    "Failed to start plugin '%s': %s",
                    plugin_id,
                    exc,
                )
                # Critical is honored only after trust verification above.
                if manifest.is_critical:
                    raise PluginLoadError(
                        f"Critical plugin '{plugin_id}' failed: {exc}"
                    ) from exc

    async def reload(
        self, name: str
    ) -> PluginInstance:
        plugin_id = name.strip().lower()
        await self.unload(plugin_id)
        await self.start(plugin_id)
        instance = self.get(plugin_id)
        if instance is None:
            raise PluginLoadError(
                f"Plugin '{plugin_id}' failed to reload"
            )
        return instance

    async def stop_all(self) -> None:
        with self._lock:
            plugin_ids = list(self._plugins)
        errors: list[str] = []
        for plugin_id in reversed(plugin_ids):
            try:
                await self.unload(plugin_id)
            except Exception as exc:
                errors.append(f"{plugin_id}: {exc}")
        if errors:
            raise RuntimeError(
                "Plugin shutdown errors: "
                + "; ".join(errors)
            )
