"""Plugin loader with explicit in-process trust and correct async lifecycle."""
from __future__ import annotations

import asyncio
import ast
import contextlib
import importlib.util
import inspect
import logging
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional, Protocol

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


class ScopedPluginHandle:
    def __init__(
        self,
        inner: PluginHandle,
        scope_factory: Callable[[], contextlib.AbstractContextManager[None]],
    ):
        self._inner = inner
        self._scope_factory = scope_factory

    async def start(self) -> None:
        with self._scope_factory():
            result = self._inner.start()
            if inspect.isawaitable(result):
                await result

    async def stop(self) -> None:
        with self._scope_factory():
            result = self._inner.stop()
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


class PluginLoader:
    """Discover and load explicitly trusted Python plugins.

    Python import executes arbitrary code before KITT's API facade can enforce
    permissions. Therefore workspace/global Python plugins are fail-closed unless
    the manifest explicitly declares ``trusted_in_process = true``. Untrusted
    extensibility should use MCP or executable skills, which already have a
    process boundary.
    """

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
        self.trust_store = trust_store or PluginTrustStore(self.workspace_root)

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
                    manifest = parse_manifest_file(manifest_file, source="global")
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

    def _build_instance(self, manifest: PluginManifest) -> PluginInstance:
        manifest_dir = (
            manifest.manifest_path.parent
            if manifest.manifest_path
            else self.workspace_root
        )
        identity = PluginIdentity(
            name=manifest.name,
            version=manifest.version,
            source=manifest.source,
            root_path=manifest_dir,
        )
        context = PluginContext(
            identity=identity,
            manifest=manifest,
            events=EventAPI(manifest.name, manifest.permissions, self.event_bus),
            hooks=HookAPI(manifest.name, manifest.permissions, self.hook_registry),
            tools=ToolAPI(manifest.name, manifest.permissions, self.tool_registry),
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
        )

    def _approved_digest(self, manifest: PluginManifest) -> str:
        if not manifest.trusted_in_process:
            raise PluginLoadError(
                f"Plugin '{manifest.name}' does not opt in to in-process execution. "
                "Review it before setting trusted_in_process=true."
            )
        approved = self.trust_store.approved_digest(manifest)
        if approved:
            return approved
        raise PluginLoadError(
            f"Plugin '{manifest.name}' is not trusted by the local user for this "
            "workspace/content hash. Run 'kitt plugins trust "
            f"{manifest.name}' after reviewing it, or use MCP/executable skills."
        )

    @staticmethod
    def _module_safe_name(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value)

    def _package_key(self, manifest: PluginManifest, approved_digest: str) -> str:
        return (
            f"kitt_plugin_{self._module_safe_name(manifest.name)}_{approved_digest}"
        )

    @contextlib.contextmanager
    def _snapshot_import_scope(self, snapshot_root: Path) -> Iterator[None]:
        snapshot = str(snapshot_root.resolve())
        workspace = str(self.workspace_root.resolve())
        removed: list[tuple[int, str]] = []
        retained: list[str] = []
        for index, entry in enumerate(sys.path):
            raw = str(entry or "")
            candidate = raw
            try:
                candidate = str(Path(raw or ".").resolve())
            except OSError:
                pass
            if candidate == workspace:
                removed.append((index, raw))
            else:
                retained.append(raw)
        sys.path[:] = [snapshot] + retained
        try:
            yield
        finally:
            if sys.path and sys.path[0] == snapshot:
                sys.path.pop(0)
            for index, entry in sorted(removed, key=lambda item: item[0]):
                insert_at = min(index, len(sys.path))
                sys.path.insert(insert_at, entry)

    @staticmethod
    def _local_top_level_names(snapshot_root: Path) -> set[str]:
        names: set[str] = set()
        for candidate in snapshot_root.rglob("*"):
            if candidate.is_dir():
                init_file = candidate / "__init__.py"
                if init_file.is_file():
                    try:
                        relative = candidate.relative_to(snapshot_root)
                    except ValueError:
                        continue
                    if relative.parts:
                        names.add(relative.parts[0])
                continue
            if candidate.suffix == ".py" and candidate.name != "__init__.py":
                try:
                    relative = candidate.relative_to(snapshot_root)
                except ValueError:
                    continue
                if relative.parts:
                    names.add(relative.parts[0].removesuffix(".py"))
        return names

    def _validate_relative_local_imports(
        self, manifest: PluginManifest, snapshot_root: Path
    ) -> None:
        local_names = self._local_top_level_names(snapshot_root)
        if not local_names:
            return
        for candidate in snapshot_root.rglob("*.py"):
            source = candidate.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(candidate))
            except SyntaxError as exc:
                raise PluginLoadError(
                    f"Plugin '{manifest.name}' has invalid Python syntax: {exc}"
                ) from exc
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in local_names:
                            raise PluginLoadError(
                                f"Plugin '{manifest.name}' must use relative imports for local module '{alias.name}'"
                            )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 0
                    and node.module
                    and node.module.split(".", 1)[0] in local_names
                ):
                    raise PluginLoadError(
                        f"Plugin '{manifest.name}' must use relative imports for local module '{node.module}'"
                    )

    def _rollback(
        self, manifest: PluginManifest, module_prefix: Optional[str] = None
    ) -> None:
        if self.hook_registry:
            self.hook_registry.unregister(plugin_id=manifest.name)
        if self.tool_registry and hasattr(
            self.tool_registry, "unregister_by_owner"
        ):
            self.tool_registry.unregister_by_owner(manifest.name)
        prefix = module_prefix or f"kitt_plugin_{self._module_safe_name(manifest.name)}"
        for key in [name for name in list(sys.modules) if name == prefix or name.startswith(f"{prefix}.")]:
            sys.modules.pop(key, None)

    async def load_async(self, manifest: PluginManifest) -> PluginInstance:
        """Load one trusted plugin and await async setup correctly."""
        approved_digest = self._approved_digest(manifest)
        snapshot_root = prepare_trusted_plugin_snapshot(
            manifest,
            approved_digest,
            self.workspace_root,
        )
        self._validate_relative_local_imports(manifest, snapshot_root)
        instance = self._build_instance(manifest)
        manifest_dir = snapshot_root

        try:
            module_name_rel, separator, function_name = manifest.entrypoint.partition(":")
            function_name = function_name if separator else "setup"
            module_name_rel = module_name_rel.replace("\\", "/").strip().removesuffix(".py")
            module_parts = [part for part in module_name_rel.split("/") if part]
            if not module_parts:
                raise PluginLoadError(
                    f"Plugin '{manifest.name}' entrypoint is invalid: {manifest.entrypoint}"
                )
            module_candidate = manifest_dir.joinpath(*module_parts)
            module_file = module_candidate.with_suffix(".py")
            if not module_file.is_file() and module_candidate.is_dir():
                module_file = module_candidate / "__init__.py"
            if not module_file.is_file():
                module_file = module_candidate
            if not module_file.is_file():
                raise PluginLoadError(
                    f"Plugin '{manifest.name}' entrypoint file not found: "
                    f"{module_file}"
                )

            package_key = self._package_key(manifest, approved_digest)
            package_module = types.ModuleType(package_key)
            package_module.__path__ = [str(snapshot_root)]
            package_module.__file__ = str(snapshot_root / "__init__.py")
            sys.modules[package_key] = package_module
            module_key = f"{package_key}.{'.'.join(module_parts)}"
            spec = importlib.util.spec_from_file_location(module_key, module_file)
            if spec is None or spec.loader is None:
                raise PluginLoadError(
                    f"Failed to create module spec for plugin '{manifest.name}' "
                    f"at {module_file}"
                )

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_key] = module
            with self._snapshot_import_scope(snapshot_root):
                spec.loader.exec_module(module)

            setup = getattr(module, function_name, None)
            if not callable(setup):
                raise PluginLoadError(
                    f"Plugin '{manifest.name}' entrypoint '{function_name}' "
                    f"is not callable in {module_file}"
                )

            with self._snapshot_import_scope(snapshot_root):
                result = setup(instance.context)
                if inspect.isawaitable(result):
                    result = await result

            if hasattr(result, "start") and hasattr(result, "stop"):
                handle: Optional[PluginHandle] = result
            elif isinstance(result, tuple) and len(result) == 2:
                handle = DefaultPluginHandle(
                    start_fn=result[0],
                    stop_fn=result[1],
                )
            else:
                handle = DefaultPluginHandle()

            instance.handle = ScopedPluginHandle(
                handle,
                lambda: self._snapshot_import_scope(snapshot_root),
            )
            instance.state = PluginState.LOADED
            return instance
        except Exception as exc:
            instance.state = PluginState.FAILED
            instance.last_error = str(exc)
            self._rollback(manifest, package_key if "package_key" in locals() else None)
            if isinstance(exc, PluginLoadError):
                raise
            raise PluginLoadError(
                f"Failed to load plugin '{manifest.name}': {exc}"
            ) from exc

    def load(self, manifest: PluginManifest) -> PluginInstance:
        """Synchronous compatibility wrapper for non-async callers."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.load_async(manifest))
        raise PluginLoadError(
            "PluginLoader.load() cannot run inside an active event loop; "
            "use await load_async()."
        )
