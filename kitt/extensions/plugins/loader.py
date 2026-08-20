"""Plugin loader for user-approved immutable snapshots."""
from __future__ import annotations

import asyncio
import ast
import importlib.util
import inspect
import logging
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Protocol

from kitt.extensions.errors import PluginLoadError
from kitt.extensions.manifest import parse_manifest_file
from kitt.extensions.models import (
    PluginIdentity,
    PluginManifest,
    PluginState,
)
from kitt.extensions.plugins.api import (
    CommandAPI,
    EventAPI,
    HookAPI,
    PluginConfigAPI,
    PluginLogger,
    ToolAPI,
)
from kitt.extensions.plugins.context import PluginContext
from kitt.extensions.plugins.security import (
    PluginTrustStore,
    prepare_trusted_plugin_snapshot,
)

logger = logging.getLogger("kitt.extensions.plugins.loader")


class PluginHandle(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class DefaultPluginHandle:
    def __init__(
        self,
        start_fn: Optional[Callable] = None,
        stop_fn: Optional[Callable] = None,
    ):
        self._start_fn = start_fn
        self._stop_fn = stop_fn

    async def start(self) -> None:
        if self._start_fn:
            result = self._start_fn()
            if inspect.isawaitable(result):
                await result

    async def stop(self) -> None:
        if self._stop_fn:
            result = self._stop_fn()
            if inspect.isawaitable(result):
                await result


@dataclass
class PluginInstance:
    manifest: PluginManifest
    identity: PluginIdentity
    context: PluginContext
    handle: Optional[PluginHandle] = None
    state: PluginState = PluginState.DISCOVERED
    last_error: Optional[str] = None
    module_prefix: Optional[str] = None
    snapshot_root: Optional[Path] = None


class PluginLoader:
    """Load only an externally-approved content snapshot."""

    def __init__(
        self,
        workspace_root: str = ".",
        global_plugins_dir: Optional[str] = None,
        event_bus=None,
        hook_registry=None,
        tool_registry=None,
        command_registry=None,
        trust_store: Optional[PluginTrustStore] = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.global_plugins_dir = Path(
            global_plugins_dir or (Path.home() / ".kitt" / "plugins")
        ).resolve()
        self.event_bus = event_bus
        self.hook_registry = hook_registry
        self.tool_registry = tool_registry
        self.command_registry = command_registry
        self.trust_store = trust_store or PluginTrustStore(
            self.workspace_root
        )

    def discover_manifests(self) -> Dict[str, PluginManifest]:
        manifests: Dict[str, PluginManifest] = {}

        if self.global_plugins_dir.is_dir():
            for child in sorted(self.global_plugins_dir.iterdir()):
                if not child.is_dir():
                    continue
                manifest_file = child / "plugin.toml"
                if not manifest_file.is_file():
                    continue
                try:
                    manifest = parse_manifest_file(
                        manifest_file, source="global"
                    )
                    manifests[manifest.name] = manifest
                except Exception as exc:
                    logger.warning(
                        "Failed to parse global plugin manifest in %s: %s",
                        child,
                        exc,
                    )

        workspace_plugins = self.workspace_root / ".kitt" / "plugins"
        if workspace_plugins.is_dir():
            for child in sorted(workspace_plugins.iterdir()):
                if not child.is_dir():
                    continue
                manifest_file = child / "plugin.toml"
                if not manifest_file.is_file():
                    continue
                try:
                    manifest = parse_manifest_file(
                        manifest_file, source="workspace"
                    )
                    if manifest.name in manifests:
                        logger.info(
                            "Workspace plugin '%s' overrides global plugin.",
                            manifest.name,
                        )
                    manifests[manifest.name] = manifest
                except Exception as exc:
                    logger.warning(
                        "Failed to parse workspace plugin manifest in %s: %s",
                        child,
                        exc,
                    )
        return manifests

    def _approved_digest(self, manifest: PluginManifest) -> str:
        if not manifest.trusted_in_process:
            raise PluginLoadError(
                f"Plugin '{manifest.name}' does not opt in to "
                "in-process execution."
            )
        approved = self.trust_store.approved_digest(manifest)
        if approved:
            return approved
        raise PluginLoadError(
            f"Plugin '{manifest.name}' is not trusted by the local user "
            "for this workspace/content hash."
        )

    @staticmethod
    def _safe_module_name(value: str) -> str:
        return "".join(
            char if char.isalnum() or char == "_" else "_"
            for char in value
        )

    def _package_key(
        self, manifest: PluginManifest, digest: str
    ) -> str:
        return (
            f"kitt_plugin_{self._safe_module_name(manifest.name)}_"
            f"{digest}"
        )

    @staticmethod
    def _local_top_level_names(snapshot_root: Path) -> set[str]:
        names: set[str] = set()
        for candidate in snapshot_root.iterdir():
            if (
                candidate.is_file()
                and candidate.suffix == ".py"
                and candidate.name != "__init__.py"
            ):
                names.add(candidate.stem)
            elif (
                candidate.is_dir()
                and (candidate / "__init__.py").is_file()
            ):
                names.add(candidate.name)
        return names

    def _validate_relative_local_imports(
        self,
        manifest: PluginManifest,
        snapshot_root: Path,
    ) -> None:
        """Local plugin modules must be imported through the synthetic package."""
        local_names = self._local_top_level_names(snapshot_root)
        if not local_names:
            return
        for candidate in snapshot_root.rglob("*.py"):
            try:
                tree = ast.parse(
                    candidate.read_text(encoding="utf-8"),
                    filename=str(candidate),
                )
            except (UnicodeDecodeError, SyntaxError) as exc:
                raise PluginLoadError(
                    f"Plugin '{manifest.name}' has invalid Python source "
                    f"{candidate.name}: {exc}"
                ) from exc

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in local_names:
                            raise PluginLoadError(
                                f"Plugin '{manifest.name}' must use "
                                f"relative import for local module "
                                f"'{alias.name}'"
                            )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 0
                    and node.module
                    and node.module.split(".", 1)[0] in local_names
                ):
                    raise PluginLoadError(
                        f"Plugin '{manifest.name}' must use relative "
                        f"import for local module '{node.module}'"
                    )

    def _build_instance(
        self,
        manifest: PluginManifest,
        snapshot_root: Path,
    ) -> PluginInstance:
        identity = PluginIdentity(
            name=manifest.name,
            version=manifest.version,
            source=manifest.source,
            root_path=snapshot_root,
        )
        context = PluginContext(
            identity=identity,
            manifest=manifest,
            events=EventAPI(
                manifest.name,
                manifest.permissions,
                self.event_bus,
            ),
            hooks=HookAPI(
                manifest.name,
                manifest.permissions,
                self.hook_registry,
            ),
            tools=ToolAPI(
                manifest.name,
                manifest.permissions,
                self.tool_registry,
            ),
            commands=CommandAPI(
                manifest.name,
                manifest.permissions,
                self.command_registry,
            ),
            config=PluginConfigAPI(manifest.name),
            logger=PluginLogger(manifest.name),
        )
        return PluginInstance(
            manifest=manifest,
            identity=identity,
            context=context,
            state=PluginState.LOADING,
            snapshot_root=snapshot_root,
        )

    def _resolve_entrypoint(
        self,
        snapshot_root: Path,
        module_name: str,
    ) -> tuple[list[str], Path, bool]:
        normalized = module_name.replace("\\", "/").strip("/")
        if normalized.endswith(".py"):
            normalized = normalized[:-3]
        if "/" not in normalized and "." in normalized:
            normalized = normalized.replace(".", "/")
        parts = [part for part in normalized.split("/") if part]
        if not parts or any(part in {".", ".."} for part in parts):
            raise PluginLoadError(
                f"Invalid plugin entrypoint module '{module_name}'"
            )

        module_candidate = snapshot_root.joinpath(*parts)
        py_file = module_candidate.with_suffix(".py")
        package_init = module_candidate / "__init__.py"
        if py_file.is_file():
            return parts, py_file, False
        if package_init.is_file():
            return parts, package_init, True
        if module_candidate.is_file():
            return parts, module_candidate, False
        raise PluginLoadError(
            f"Plugin entrypoint file not found for '{module_name}'"
        )

    @staticmethod
    def _ensure_package(module_name: str, package_path: Path) -> None:
        if module_name in sys.modules:
            return
        module = types.ModuleType(module_name)
        module.__path__ = [str(package_path)]
        module.__package__ = module_name
        module.__file__ = str(package_path / "__init__.py")
        sys.modules[module_name] = module

    def unload_instance(self, instance: PluginInstance) -> None:
        prefix = instance.module_prefix
        if not prefix:
            return
        for name in list(sys.modules):
            if name == prefix or name.startswith(prefix + "."):
                sys.modules.pop(name, None)

    def _rollback(self, instance: PluginInstance) -> None:
        if self.hook_registry:
            self.hook_registry.unregister(
                plugin_id=instance.manifest.name
            )
        if (
            self.tool_registry
            and hasattr(self.tool_registry, "unregister_by_owner")
        ):
            self.tool_registry.unregister_by_owner(
                instance.manifest.name
            )
        self.unload_instance(instance)

    async def load_async(
        self, manifest: PluginManifest
    ) -> PluginInstance:
        approved_digest = self._approved_digest(manifest)
        snapshot_root = prepare_trusted_plugin_snapshot(
            manifest,
            approved_digest,
            self.workspace_root,
        )
        self._validate_relative_local_imports(
            manifest, snapshot_root
        )
        instance = self._build_instance(
            manifest, snapshot_root
        )
        package_key = self._package_key(
            manifest, approved_digest
        )
        instance.module_prefix = package_key

        try:
            module_name, separator, function_name = (
                manifest.entrypoint.partition(":")
            )
            function_name = function_name if separator else "setup"
            parts, module_file, is_package = (
                self._resolve_entrypoint(
                    snapshot_root, module_name
                )
            )

            self._ensure_package(
                package_key, snapshot_root
            )
            for depth in range(1, len(parts)):
                package_name = (
                    package_key
                    + "."
                    + ".".join(parts[:depth])
                )
                package_path = snapshot_root.joinpath(
                    *parts[:depth]
                )
                self._ensure_package(
                    package_name, package_path
                )

            module_key = package_key + "." + ".".join(parts)
            kwargs = {}
            if is_package:
                kwargs["submodule_search_locations"] = [
                    str(module_file.parent)
                ]
            spec = importlib.util.spec_from_file_location(
                module_key,
                module_file,
                **kwargs,
            )
            if spec is None or spec.loader is None:
                raise PluginLoadError(
                    f"Failed creating module spec for "
                    f"'{manifest.name}'"
                )

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_key] = module
            spec.loader.exec_module(module)

            setup = getattr(module, function_name, None)
            if not callable(setup):
                raise PluginLoadError(
                    f"Plugin '{manifest.name}' entrypoint "
                    f"'{function_name}' is not callable"
                )

            result = setup(instance.context)
            if inspect.isawaitable(result):
                result = await result

            if hasattr(result, "start") and hasattr(result, "stop"):
                handle: PluginHandle = result
            elif isinstance(result, tuple) and len(result) == 2:
                handle = DefaultPluginHandle(
                    start_fn=result[0],
                    stop_fn=result[1],
                )
            else:
                handle = DefaultPluginHandle()

            instance.handle = handle
            instance.state = PluginState.LOADED
            return instance
        except Exception as exc:
            instance.state = PluginState.FAILED
            instance.last_error = str(exc)
            self._rollback(instance)
            if isinstance(exc, PluginLoadError):
                raise
            raise PluginLoadError(
                f"Failed to load plugin '{manifest.name}': {exc}"
            ) from exc

    def load(
        self, manifest: PluginManifest
    ) -> PluginInstance:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.load_async(manifest))
        raise PluginLoadError(
            "PluginLoader.load() cannot run inside an active event loop; "
            "use await load_async()."
        )
