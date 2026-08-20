"""Local user-controlled trust, snapshots and activation state for Python plugins."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import stat
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
_MAX_STATE_BYTES = 1024 * 1024


def _workspace_key(workspace_root: str | Path) -> str:
    canonical = str(Path(workspace_root).resolve())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _set_private_permissions(path: Path, *, readonly: bool = False) -> None:
    if os.name == "nt":
        return
    try:
        if path.is_dir():
            os.chmod(path, 0o700)
        else:
            os.chmod(path, 0o400 if readonly else 0o600)
    except OSError:
        pass


def _make_tree_writable(root: Path) -> None:
    if not root.exists() or os.name == "nt":
        return
    for candidate in root.rglob("*"):
        _set_private_permissions(candidate, readonly=False)
    _set_private_permissions(root, readonly=False)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _set_private_permissions(path.parent)
    data = json.dumps(
        payload, indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _set_private_permissions(tmp)
        os.replace(tmp, path)
        _set_private_permissions(path)
    finally:
        tmp.unlink(missing_ok=True)


def _state_path(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(str(path), flags)
    except FileNotFoundError:
        return dict(default)
    except OSError as exc:
        raise PluginLoadError(
            f"Unable to securely open plugin state file {path}: {exc}"
        ) from exc

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PluginLoadError(
                f"Plugin state file must be regular: {path}"
            )
        if os.name != "nt":
            if st.st_uid != os.getuid():
                raise PluginLoadError(
                    f"Plugin state file owner mismatch: {path}"
                )
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise PluginLoadError(
                    f"Plugin state file permissions must be 0600: {path}"
                )
        if st.st_size > _MAX_STATE_BYTES:
            raise PluginLoadError(
                f"Plugin state file exceeds {_MAX_STATE_BYTES} bytes: {path}"
            )

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                fd,
                min(64 * 1024, (_MAX_STATE_BYTES + 1) - total),
            )
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_STATE_BYTES:
                raise PluginLoadError(
                    f"Plugin state file exceeds {_MAX_STATE_BYTES} bytes: {path}"
                )
            chunks.append(chunk)
    finally:
        os.close(fd)

    try:
        data = json.loads(b"".join(chunks).decode("utf-8"))
    except Exception as exc:
        raise PluginLoadError(
            f"Invalid plugin security state file {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise PluginLoadError(
            f"Plugin security state file {path} must contain an object"
        )
    return data


class _InterprocessLock:
    def __init__(
        self,
        path: Path,
        timeout_seconds: float = _LOCK_TIMEOUT_SECONDS,
    ):
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _set_private_permissions(self.path.parent)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(self.path), flags, 0o600)
        except OSError as exc:
            raise PluginLoadError(
                f"Unable to securely open plugin state lock {self.path}: {exc}"
            ) from exc
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise PluginLoadError(
                f"Plugin state lock must be regular: {self.path}"
            )
        self._handle = os.fdopen(fd, "r+b", buffering=0)
        _set_private_permissions(self.path)
        deadline = time.monotonic() + self.timeout_seconds

        if os.name == "nt":
            import msvcrt

            self._handle.seek(0, os.SEEK_END)
            if self._handle.tell() == 0:
                self._handle.write(b"\0")
                self._handle.flush()
            while True:
                try:
                    self._handle.seek(0)
                    msvcrt.locking(
                        self._handle.fileno(), msvcrt.LK_NBLCK, 1
                    )
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out acquiring lock {self.path}"
                        )
                    time.sleep(0.05)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(
                        self._handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out acquiring lock {self.path}"
                        )
                    time.sleep(0.05)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(
                    self._handle.fileno(), msvcrt.LK_UNLCK, 1
                )
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def _trusted_plugin_cache_root() -> Path:
    preferred = Path.home() / ".kitt" / "cache" / "trusted-plugins"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        _set_private_permissions(preferred)
        return preferred.resolve()
    except OSError:
        owner = (
            str(os.getuid())
            if hasattr(os, "getuid")
            else str(os.getpid())
        )
        fallback = (
            Path(tempfile.gettempdir())
            / f"kitt-{owner}"
            / "trusted-plugins"
        )
        fallback.mkdir(parents=True, exist_ok=True)
        _set_private_permissions(fallback.parent)
        _set_private_permissions(fallback)
        return fallback.resolve()


def _iter_plugin_files(root: Path, manifest_name: str):
    file_count = 0
    total_bytes = 0
    for candidate in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if candidate.is_symlink():
            raise PluginLoadError(
                f"Plugin '{manifest_name}' contains a symlink: {candidate}"
            )
        if not candidate.is_file():
            continue
        if (
            "__pycache__" in candidate.parts
            or candidate.suffix in {".pyc", ".pyo"}
        ):
            continue

        try:
            stat_result = candidate.stat(follow_symlinks=False)
        except TypeError:
            stat_result = candidate.stat()

        if os.name != "nt" and getattr(stat_result, "st_nlink", 1) > 1:
            raise PluginLoadError(
                f"Plugin '{manifest_name}' contains a hard-linked file: "
                f"{candidate}"
            )

        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise PluginLoadError(
                f"Plugin '{manifest_name}' contains invalid path {candidate}"
            ) from exc
        if ".." in relative.parts:
            raise PluginLoadError(
                f"Plugin '{manifest_name}' contains path traversal: {relative}"
            )

        file_count += 1
        if file_count > _MAX_PLUGIN_FILES:
            raise PluginLoadError(
                f"Plugin '{manifest_name}' exceeds {_MAX_PLUGIN_FILES} files"
            )
        size = stat_result.st_size
        if size > _MAX_PLUGIN_FILE_BYTES:
            raise PluginLoadError(
                f"Plugin file {candidate.name} exceeds "
                f"{_MAX_PLUGIN_FILE_BYTES} bytes"
            )
        total_bytes += size
        if total_bytes > _MAX_PLUGIN_TOTAL_BYTES:
            raise PluginLoadError(
                f"Plugin '{manifest_name}' exceeds "
                f"{_MAX_PLUGIN_TOTAL_BYTES} total bytes"
            )
        yield candidate, relative


def plugin_content_digest(manifest: PluginManifest) -> str:
    if manifest.manifest_path is None:
        raise PluginLoadError(
            f"Plugin '{manifest.name}' has no manifest path"
        )
    root = manifest.manifest_path.parent.resolve()
    if not root.is_dir():
        raise PluginLoadError(f"Plugin root does not exist: {root}")

    digest = hashlib.sha256()
    for candidate, relative_path in _iter_plugin_files(root, manifest.name):
        data = candidate.read_bytes()
        relative = relative_path.as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _snapshot_manifest(
    manifest: PluginManifest, root: Path
) -> PluginManifest:
    if manifest.manifest_path is None:
        raise PluginLoadError(
            f"Plugin '{manifest.name}' has no manifest path"
        )
    return PluginManifest(
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
        manifest_path=root / manifest.manifest_path.name,
        trusted_in_process=manifest.trusted_in_process,
    )


def _verify_snapshot(
    manifest: PluginManifest,
    root: Path,
    approved_digest: str,
) -> bool:
    if not root.is_dir():
        return False
    try:
        actual = plugin_content_digest(_snapshot_manifest(manifest, root))
    except Exception:
        return False
    return hmac.compare_digest(actual, approved_digest)


def prepare_trusted_plugin_snapshot(
    manifest: PluginManifest,
    approved_digest: str,
    workspace_root: str | Path,
) -> Path:
    """Materialize a content-addressed snapshot with serialized verification."""
    if manifest.manifest_path is None:
        raise PluginLoadError(
            f"Plugin '{manifest.name}' has no manifest path"
        )
    source_root = manifest.manifest_path.parent.resolve()
    if not hmac.compare_digest(
        plugin_content_digest(manifest), approved_digest
    ):
        raise PluginLoadError(
            f"Plugin '{manifest.name}' content changed since trust approval."
        )

    cache_root = (
        _trusted_plugin_cache_root()
        / _workspace_key(workspace_root)
        / manifest.name
    ).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    _set_private_permissions(cache_root)
    final_root = cache_root / approved_digest
    lock_path = cache_root / f".{approved_digest}.snapshot.lock"

    with _InterprocessLock(lock_path):
        if final_root.exists():
            if _verify_snapshot(manifest, final_root, approved_digest):
                return final_root
            _make_tree_writable(final_root)
            shutil.rmtree(final_root, ignore_errors=False)

        temp_root = Path(
            tempfile.mkdtemp(
                prefix=f"{approved_digest[:12]}-",
                dir=str(cache_root),
            )
        )
        _set_private_permissions(temp_root)
        try:
            for source_path, relative_path in _iter_plugin_files(
                source_root, manifest.name
            ):
                target_path = temp_root / relative_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                _set_private_permissions(target_path.parent)
                with source_path.open("rb") as src, target_path.open(
                    "xb"
                ) as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                _set_private_permissions(target_path)

            if not _verify_snapshot(manifest, temp_root, approved_digest):
                raise PluginLoadError(
                    f"Plugin '{manifest.name}' changed during snapshot "
                    "materialization."
                )

            for path in sorted(
                temp_root.rglob("*"),
                key=lambda p: p.as_posix(),
                reverse=True,
            ):
                _set_private_permissions(path, readonly=path.is_file())
            _set_private_permissions(temp_root)

            try:
                os.replace(temp_root, final_root)
            except OSError:
                # A concurrent creator may have won. Never trust by path name.
                if not _verify_snapshot(
                    manifest, final_root, approved_digest
                ):
                    raise
                _make_tree_writable(temp_root)
                shutil.rmtree(temp_root, ignore_errors=True)

            if not _verify_snapshot(manifest, final_root, approved_digest):
                _make_tree_writable(final_root)
                shutil.rmtree(final_root, ignore_errors=True)
                raise PluginLoadError(
                    f"Trusted snapshot verification failed for "
                    f"'{manifest.name}'."
                )
            return final_root
        except Exception:
            if temp_root.exists():
                _make_tree_writable(temp_root)
                shutil.rmtree(temp_root, ignore_errors=True)
            raise


class PluginTrustStore:
    """Trust is local-user granted, workspace-scoped and hash-bound."""

    def __init__(
        self,
        workspace_root: str | Path,
        path: Optional[str | Path] = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_key = _workspace_key(self.workspace_root)
        self.path = _state_path(
            path
            or (
                Path.home()
                / ".kitt"
                / "security"
                / "plugin-trust.json"
            )
        )
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _data(self) -> dict[str, Any]:
        data = _read_json(
            self.path,
            {"version": _STATE_VERSION, "workspaces": {}},
        )
        data.setdefault("version", _STATE_VERSION)
        data.setdefault("workspaces", {})
        return data

    @staticmethod
    def _plugin_key(manifest: PluginManifest) -> str:
        return f"{manifest.source}:{manifest.name}"

    def approved_digest(
        self, manifest: PluginManifest
    ) -> Optional[str]:
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
        if (
            not digest
            or str(record.get("version") or "") != manifest.version
        ):
            return None
        return digest

    def grant(self, manifest: PluginManifest) -> str:
        if not manifest.trusted_in_process:
            raise PluginLoadError(
                f"Plugin '{manifest.name}' does not opt in to in-process "
                "execution."
            )
        digest = plugin_content_digest(manifest)
        with _InterprocessLock(self.lock_path):
            data = self._data()
            workspace = data["workspaces"].setdefault(
                self.workspace_key, {"plugins": {}}
            )
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
            "trusted": bool(
                approved and hmac.compare_digest(approved, actual)
            ),
            "digest": actual,
            "approved_digest": approved,
        }


class PluginStateStore:
    """Persist explicit enable/disable choices outside repository content."""

    def __init__(
        self,
        workspace_root: str | Path,
        path: Optional[str | Path] = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_key = _workspace_key(self.workspace_root)
        self.path = _state_path(
            path
            or (
                Path.home()
                / ".kitt"
                / "state"
                / "plugin-state.json"
            )
        )
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _data(self) -> dict[str, Any]:
        data = _read_json(
            self.path,
            {"version": _STATE_VERSION, "workspaces": {}},
        )
        data.setdefault("version", _STATE_VERSION)
        data.setdefault("workspaces", {})
        return data

    def load(self) -> tuple[set[str], set[str]]:
        workspace = (
            self._data()
            .get("workspaces", {})
            .get(self.workspace_key, {})
        )
        enabled = {
            str(item).strip().lower()
            for item in workspace.get("enabled", [])
        }
        disabled = {
            str(item).strip().lower()
            for item in workspace.get("disabled", [])
        }
        return enabled, disabled

    def set_enabled(self, name: str, enabled: bool) -> None:
        plugin_id = str(name).strip().lower()
        with _InterprocessLock(self.lock_path):
            data = self._data()
            workspace = data["workspaces"].setdefault(
                self.workspace_key,
                {"enabled": [], "disabled": []},
            )
            explicit_enabled = {
                str(item).strip().lower()
                for item in workspace.get("enabled", [])
            }
            disabled = {
                str(item).strip().lower()
                for item in workspace.get("disabled", [])
            }
            if enabled:
                explicit_enabled.add(plugin_id)
                disabled.discard(plugin_id)
            else:
                disabled.add(plugin_id)
                explicit_enabled.discard(plugin_id)
            workspace["enabled"] = sorted(explicit_enabled)
            workspace["disabled"] = sorted(disabled)
            _atomic_write_json(self.path, data)
