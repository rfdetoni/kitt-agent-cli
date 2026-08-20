"""Local, user-controlled trust and activation state for Python plugins."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from kitt.extensions.errors import PluginLoadError
from kitt.extensions.models import PluginManifest

_STATE_VERSION = 1
_MAX_PLUGIN_FILES = 4096
_MAX_PLUGIN_FILE_BYTES = 8 * 1024 * 1024
_MAX_PLUGIN_TOTAL_BYTES = 64 * 1024 * 1024
_LOCK_TIMEOUT_SECONDS = 5.0


def _workspace_key(workspace_root: str | Path) -> str:
    canonical = str(Path(workspace_root).resolve())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)
        if os.name != "nt":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    finally:
        tmp.unlink(missing_ok=True)


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PluginLoadError(f"Invalid plugin security state file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PluginLoadError(f"Plugin security state file {path} must contain an object")
    return data


class _InterprocessLock:
    def __init__(self, path: Path, timeout_seconds: float = _LOCK_TIMEOUT_SECONDS):
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        deadline = time.monotonic() + self.timeout_seconds
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out acquiring lock {self.path}")
                    time.sleep(0.05)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out acquiring lock {self.path}")
                    time.sleep(0.05)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def _set_private_permissions(path: Path, *, readonly: bool = False) -> None:
    if os.name == "nt":
        return
    mode = 0o700
    if path.is_file():
        mode = 0o400 if readonly else 0o600
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _trusted_plugin_cache_root(workspace_root: str | Path) -> Path:
    preferred = Path.home() / ".kitt" / "cache" / "trusted-plugins"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        _set_private_permissions(preferred)
        return preferred
    except OSError:
        fallback = (
            Path(workspace_root).resolve() / ".kitt" / "cache" / "trusted-plugins"
        )
        fallback.mkdir(parents=True, exist_ok=True)
        _set_private_permissions(fallback)
        return fallback


def _iter_plugin_files(root: Path, manifest_name: str):
    file_count = 0
    total_bytes = 0
    for candidate in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if candidate.is_symlink():
            raise PluginLoadError(
                f"Plugin '{manifest_name}' contains a symlink and cannot be trusted in-process: {candidate}"
            )
        if not candidate.is_file():
            continue
        if "__pycache__" in candidate.parts or candidate.suffix in {".pyc", ".pyo"}:
            continue
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise PluginLoadError(
                f"Plugin '{manifest_name}' contains an invalid path: {candidate}"
            ) from exc
        if ".." in relative.parts:
            raise PluginLoadError(
                f"Plugin '{manifest_name}' contains path traversal: {relative}"
            )
        file_count += 1
        if file_count > _MAX_PLUGIN_FILES:
            raise PluginLoadError(f"Plugin '{manifest_name}' exceeds {_MAX_PLUGIN_FILES} files")
        size = candidate.stat().st_size
        if size > _MAX_PLUGIN_FILE_BYTES:
            raise PluginLoadError(
                f"Plugin file {candidate.name} exceeds {_MAX_PLUGIN_FILE_BYTES} bytes"
            )
        total_bytes += size
        if total_bytes > _MAX_PLUGIN_TOTAL_BYTES:
            raise PluginLoadError(
                f"Plugin '{manifest_name}' exceeds {_MAX_PLUGIN_TOTAL_BYTES} total bytes"
            )
        yield candidate, relative


def plugin_content_digest(manifest: PluginManifest) -> str:
    """Hash complete plugin tree, rejecting symlinks and oversized content."""
    if manifest.manifest_path is None:
        raise PluginLoadError(f"Plugin '{manifest.name}' has no manifest path")
    root = manifest.manifest_path.parent.resolve()
    if not root.is_dir():
        raise PluginLoadError(f"Plugin root does not exist: {root}")

    digest = hashlib.sha256()
    for candidate, relative_path in _iter_plugin_files(root, manifest.name):
        size = candidate.stat().st_size
        relative = relative_path.as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(size.to_bytes(8, "big"))
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def prepare_trusted_plugin_snapshot(
    manifest: PluginManifest,
    approved_digest: str,
    workspace_root: str | Path,
) -> Path:
    if manifest.manifest_path is None:
        raise PluginLoadError(f"Plugin '{manifest.name}' has no manifest path")
    source_root = manifest.manifest_path.parent.resolve()
    if plugin_content_digest(manifest) != approved_digest:
        raise PluginLoadError(
            f"Plugin '{manifest.name}' content changed since trust approval."
        )

    cache_root = (
        _trusted_plugin_cache_root(workspace_root)
        / _workspace_key(workspace_root)
        / manifest.name
    ).resolve()
    final_root = cache_root / approved_digest
    if final_root.is_dir():
        return final_root

    cache_root.mkdir(parents=True, exist_ok=True)
    _set_private_permissions(cache_root)
    temp_root = Path(
        tempfile.mkdtemp(prefix=f"{approved_digest[:12]}-", dir=str(cache_root))
    )
    try:
        for source_path, relative_path in _iter_plugin_files(source_root, manifest.name):
            target_path = temp_root / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            _set_private_permissions(target_path.parent)
            with source_path.open("rb") as src, target_path.open("wb") as dst:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(chunk)
            _set_private_permissions(target_path)

        snapshot_manifest = PluginManifest(
            name=manifest.name,
            version=manifest.version,
            api_version=manifest.api_version,
            entrypoint=manifest.entrypoint,
            permissions=set(manifest.permissions),
            description=manifest.description,
            author=manifest.author,
            homepage=manifest.homepage,
            dependencies=list(manifest.dependencies),
            requires_kitt=manifest.requires_kitt,
            enabled_by_default=manifest.enabled_by_default,
            is_critical=manifest.is_critical,
            source=manifest.source,
            manifest_path=temp_root / manifest.manifest_path.name,
            trusted_in_process=manifest.trusted_in_process,
        )
        snapshot_digest = plugin_content_digest(snapshot_manifest)
        if not hmac.compare_digest(snapshot_digest, approved_digest):
            raise PluginLoadError(
                f"Plugin '{manifest.name}' changed during snapshot materialization."
            )

        for path in sorted(temp_root.rglob("*"), key=lambda p: p.as_posix(), reverse=True):
            _set_private_permissions(path, readonly=path.is_file())
        try:
            temp_root.replace(final_root)
        except OSError:
            if final_root.is_dir():
                shutil.rmtree(temp_root, ignore_errors=True)
            else:
                raise
        return final_root
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


class PluginTrustStore:
    """Trust is local-user granted, outside repository, content-hash bound."""

    def __init__(
        self,
        workspace_root: str | Path,
        path: Optional[str | Path] = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_key = _workspace_key(self.workspace_root)
        self.path = Path(
            path or (Path.home() / ".kitt" / "security" / "plugin-trust.json")
        ).expanduser().resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _data(self) -> dict[str, Any]:
        data = _read_json(self.path, {"version": _STATE_VERSION, "workspaces": {}})
        data.setdefault("version", _STATE_VERSION)
        data.setdefault("workspaces", {})
        return data

    @staticmethod
    def _plugin_key(manifest: PluginManifest) -> str:
        return f"{manifest.source}:{manifest.name}"

    def approved_digest(self, manifest: PluginManifest) -> Optional[str]:
        data = self._data()
        record = (
            data.get("workspaces", {})
            .get(self.workspace_key, {})
            .get("plugins", {})
            .get(self._plugin_key(manifest))
        )
        if not isinstance(record, dict):
            return None
        digest = str(record.get("digest") or "")
        if not digest or str(record.get("version") or "") != manifest.version:
            return None
        return digest

    def grant(self, manifest: PluginManifest) -> str:
        if not manifest.trusted_in_process:
            raise PluginLoadError(
                f"Plugin '{manifest.name}' does not opt in to in-process execution. "
                "Set trusted_in_process=true only after reviewing the plugin."
            )
        digest = plugin_content_digest(manifest)
        with _InterprocessLock(self.lock_path):
            data = self._data()
            workspace = data["workspaces"].setdefault(self.workspace_key, {"plugins": {}})
            plugins = workspace.setdefault("plugins", {})
            plugins[self._plugin_key(manifest)] = {
                "version": manifest.version,
                "digest": digest,
            }
            _atomic_write_json(self.path, data)
        return digest

    def revoke(self, name: str) -> bool:
        plugin_name = str(name).strip().lower()
        removed = False
        with _InterprocessLock(self.lock_path):
            data = self._data()
            workspace = data["workspaces"].get(self.workspace_key, {})
            plugins = workspace.get("plugins", {})
            for key in list(plugins):
                if key.split(":", 1)[-1] == plugin_name:
                    plugins.pop(key, None)
                    removed = True
            if removed:
                _atomic_write_json(self.path, data)
        return removed

    def is_trusted(self, manifest: PluginManifest) -> bool:
        if not manifest.trusted_in_process:
            return False
        expected = self.approved_digest(manifest)
        if not expected:
            return False
        actual = plugin_content_digest(manifest)
        return hmac.compare_digest(expected, actual)

    def status(self, manifest: PluginManifest) -> dict[str, Any]:
        actual = plugin_content_digest(manifest)
        approved = self.approved_digest(manifest)
        return {
            "trusted": bool(approved and hmac.compare_digest(approved, actual)),
            "digest": actual,
            "approved_digest": approved,
        }


class PluginStateStore:
    """Persist enable/disable choices in local user state, never in trust data."""

    def __init__(
        self,
        workspace_root: str | Path,
        path: Optional[str | Path] = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_key = _workspace_key(self.workspace_root)
        self.path = Path(
            path or (Path.home() / ".kitt" / "state" / "plugin-state.json")
        ).expanduser().resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _data(self) -> dict[str, Any]:
        data = _read_json(self.path, {"version": _STATE_VERSION, "workspaces": {}})
        data.setdefault("version", _STATE_VERSION)
        data.setdefault("workspaces", {})
        return data

    def load(self) -> tuple[set[str], set[str]]:
        workspace = self._data().get("workspaces", {}).get(self.workspace_key, {})
        enabled = {str(x).strip().lower() for x in workspace.get("enabled", [])}
        disabled = {str(x).strip().lower() for x in workspace.get("disabled", [])}
        return enabled, disabled

    def set_enabled(self, name: str, enabled: bool) -> None:
        plugin_id = str(name).strip().lower()
        with _InterprocessLock(self.lock_path):
            data = self._data()
            workspace = data["workspaces"].setdefault(
                self.workspace_key, {"enabled": [], "disabled": []}
            )
            explicit_enabled = {str(x).strip().lower() for x in workspace.get("enabled", [])}
            disabled = {str(x).strip().lower() for x in workspace.get("disabled", [])}
            if enabled:
                explicit_enabled.add(plugin_id)
                disabled.discard(plugin_id)
            else:
                disabled.add(plugin_id)
                explicit_enabled.discard(plugin_id)
            workspace["enabled"] = sorted(explicit_enabled)
            workspace["disabled"] = sorted(disabled)
            _atomic_write_json(self.path, data)
