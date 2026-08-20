"""Local, user-controlled trust and activation state for Python plugins."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from kitt.extensions.errors import PluginLoadError
from kitt.extensions.models import PluginManifest

_STATE_VERSION = 1
_MAX_PLUGIN_FILES = 4096
_MAX_PLUGIN_FILE_BYTES = 8 * 1024 * 1024
_MAX_PLUGIN_TOTAL_BYTES = 64 * 1024 * 1024


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


def plugin_content_digest(manifest: PluginManifest) -> str:
    """Hash the complete plugin tree, rejecting symlinks and oversized content."""
    if manifest.manifest_path is None:
        raise PluginLoadError(f"Plugin '{manifest.name}' has no manifest path")
    root = manifest.manifest_path.parent.resolve()
    if not root.is_dir():
        raise PluginLoadError(f"Plugin root does not exist: {root}")

    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for candidate in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if candidate.is_symlink():
            raise PluginLoadError(
                f"Plugin '{manifest.name}' contains a symlink and cannot be trusted in-process: {candidate}"
            )
        if not candidate.is_file():
            continue
        if "__pycache__" in candidate.parts or candidate.suffix in {".pyc", ".pyo"}:
            continue
        file_count += 1
        if file_count > _MAX_PLUGIN_FILES:
            raise PluginLoadError(f"Plugin '{manifest.name}' exceeds {_MAX_PLUGIN_FILES} files")
        size = candidate.stat().st_size
        if size > _MAX_PLUGIN_FILE_BYTES:
            raise PluginLoadError(
                f"Plugin file {candidate.name} exceeds {_MAX_PLUGIN_FILE_BYTES} bytes"
            )
        total_bytes += size
        if total_bytes > _MAX_PLUGIN_TOTAL_BYTES:
            raise PluginLoadError(
                f"Plugin '{manifest.name}' exceeds {_MAX_PLUGIN_TOTAL_BYTES} total bytes"
            )
        relative = candidate.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(size.to_bytes(8, "big"))
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


class PluginTrustStore:
    """Trust is local-user granted, outside the repository, and content-hash bound."""

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

    def _data(self) -> dict[str, Any]:
        data = _read_json(self.path, {"version": _STATE_VERSION, "workspaces": {}})
        data.setdefault("version", _STATE_VERSION)
        data.setdefault("workspaces", {})
        return data

    @staticmethod
    def _plugin_key(manifest: PluginManifest) -> str:
        return f"{manifest.source}:{manifest.name}"

    def grant(self, manifest: PluginManifest) -> str:
        if not manifest.trusted_in_process:
            raise PluginLoadError(
                f"Plugin '{manifest.name}' does not opt in to in-process execution. "
                "Set trusted_in_process=true only after reviewing the plugin."
            )
        digest = plugin_content_digest(manifest)
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
        data = self._data()
        workspace = data["workspaces"].get(self.workspace_key, {})
        plugins = workspace.get("plugins", {})
        removed = False
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
        data = self._data()
        record = (
            data.get("workspaces", {})
            .get(self.workspace_key, {})
            .get("plugins", {})
            .get(self._plugin_key(manifest))
        )
        if not isinstance(record, dict):
            return False
        expected = str(record.get("digest") or "")
        if not expected or str(record.get("version") or "") != manifest.version:
            return False
        actual = plugin_content_digest(manifest)
        return hmac.compare_digest(expected, actual)

    def status(self, manifest: PluginManifest) -> dict[str, Any]:
        actual = plugin_content_digest(manifest)
        data = self._data()
        record = (
            data.get("workspaces", {})
            .get(self.workspace_key, {})
            .get("plugins", {})
            .get(self._plugin_key(manifest))
        )
        return {
            "trusted": self.is_trusted(manifest),
            "digest": actual,
            "approved_digest": record.get("digest") if isinstance(record, dict) else None,
        }


class PluginStateStore:
    """Persist enable/disable choices in local user state, never in repository trust data."""

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
