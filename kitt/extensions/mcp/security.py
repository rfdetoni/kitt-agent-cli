"""Local trust store for repository-provided MCP configuration."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from kitt.extensions.errors import MCPError
from kitt.extensions.mcp.models import MCPServerConfig

_STATE_VERSION = 1
_LOCK_TIMEOUT_SECONDS = 5.0


def _workspace_key(root: str | Path) -> str:
    return hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()


def mcp_config_digest(config: MCPServerConfig) -> str:
    payload = {
        "server_id": str(config.server_id).strip().lower(),
        "transport": str(config.transport).strip().lower(),
        "command": config.command,
        "args": list(config.args),
        "env": dict(sorted(config.env.items())),
        "url": config.url,
        "headers": dict(sorted(config.headers.items())),
        "trust": config.trust,
        "allow_tools": config.allow_tools,
        "deny_tools": list(config.deny_tools),
        "timeout_seconds": float(config.timeout_seconds),
        "max_output_bytes": int(config.max_output_bytes),
        "source": config.source,
    }
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
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


class _InterprocessLock:
    def __init__(self, path: Path, timeout: float = _LOCK_TIMEOUT_SECONDS):
        self.path = path
        self.timeout = timeout
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+b")
        deadline = time.monotonic() + self.timeout
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"\0")
                self.handle.flush()
            while True:
                try:
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out acquiring lock {self.path}")
                    time.sleep(0.05)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(
                        self.handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out acquiring lock {self.path}")
                    time.sleep(0.05)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


class MCPTrustStore:
    """Repository MCP config is untrusted until the exact config is approved."""

    def __init__(
        self,
        workspace_root: str | Path,
        path: Optional[str | Path] = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_key = _workspace_key(self.workspace_root)
        self.path = Path(
            path or (Path.home() / ".kitt" / "security" / "mcp-trust.json")
        ).expanduser().resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _data(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": _STATE_VERSION, "workspaces": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise MCPError(f"Invalid MCP trust store {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise MCPError("MCP trust store must contain an object")
        data.setdefault("version", _STATE_VERSION)
        data.setdefault("workspaces", {})
        return data

    def is_trusted(self, config: MCPServerConfig) -> bool:
        if config.source != "workspace":
            return True
        record = (
            self._data()
            .get("workspaces", {})
            .get(self.workspace_key, {})
            .get(str(config.server_id).strip().lower())
        )
        if not isinstance(record, dict):
            return False
        expected = str(record.get("digest") or "")
        return bool(expected) and hmac.compare_digest(
            expected, mcp_config_digest(config)
        )

    def grant(self, config: MCPServerConfig) -> str:
        digest = mcp_config_digest(config)
        if config.source != "workspace":
            return digest
        server_id = str(config.server_id).strip().lower()
        with _InterprocessLock(self.lock_path):
            data = self._data()
            workspace = data["workspaces"].setdefault(self.workspace_key, {})
            workspace[server_id] = {"digest": digest}
            _atomic_write_json(self.path, data)
        return digest

    def revoke(self, server_id: str) -> bool:
        server_id = str(server_id).strip().lower()
        removed = False
        with _InterprocessLock(self.lock_path):
            data = self._data()
            workspace = data.get("workspaces", {}).get(self.workspace_key, {})
            if server_id in workspace:
                del workspace[server_id]
                removed = True
                _atomic_write_json(self.path, data)
        return removed

    def assert_trusted(self, config: MCPServerConfig) -> None:
        if not self.is_trusted(config):
            raise MCPError(
                f"Workspace MCP server '{config.server_id}' is untrusted or changed. "
                f"Review it and run 'kitt mcp trust {config.server_id}'."
            )
