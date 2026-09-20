"""Parent-side isolated plugin worker client using authenticated JSON IPC."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import shutil
from typing import Any

from kitt.security.execution_sandbox import ExecutionSandbox
from kitt.security.private_state import ensure_private_dir, kitt_home


logger = logging.getLogger("kitt.extensions.plugins.worker")

_MAX_MESSAGE_BYTES = 4 * 1024 * 1024
_SECRET_ENV_RE = re.compile(
    r"(?i)(^|_)(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|"
    r"CREDENTIAL|ACCESS_?KEY|AUTH_?TOKEN|REFRESH_?TOKEN)($|_)"
)
_DANGEROUS_ENV_NAMES = {
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "BASH_ENV",
    "ENV",
    "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES",
    "SSH_AUTH_SOCK",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "AWS_SHARED_CREDENTIALS_FILE",
    "GOOGLE_APPLICATION_CREDENTIALS",
}


def _sanitized_worker_env() -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in _DANGEROUS_ENV_NAMES:
            continue
        if upper.startswith(("DYLD_", "GIT_CONFIG_")):
            continue
        if _SECRET_ENV_RE.search(upper):
            continue
        result[key] = value
    result["PYTHONNOUSERSITE"] = "1"
    result["PYTHONSAFEPATH"] = "1"
    result["GIT_TERMINAL_PROMPT"] = "0"
    return result


@dataclass
class PluginWorkerLaunchPlan:
    argv: list[str]
    env: dict[str, str]
    backend: str = "process"
    strong: bool = False
    network_isolated: bool = False
    workspace_access: str = "none"
    metadata: dict[str, Any] = field(default_factory=dict)


class PluginWorkerSandbox:
    """Permission-scoped Linux sandbox planner for plugin workers."""

    def __init__(
        self,
        workspace_root: str | Path,
        permissions: set[str] | list[str] | tuple[str, ...],
        *,
        bwrap_path: str | None = None,
        bwrap_version: tuple[int, int, int] | None = None,
        auto_detect: bool = True,
        home_dir: str | Path | None = None,
    ):
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.permissions = frozenset(str(item) for item in permissions)
        self.home = (
            Path(home_dir).expanduser().resolve()
            if home_dir is not None
            else Path.home().expanduser().resolve()
        )
        self._sandbox = ExecutionSandbox(
            self.workspace_root,
            bwrap_path=bwrap_path,
            bwrap_version=bwrap_version,
            auto_detect=auto_detect,
            home_dir=self.home,
        )

    @staticmethod
    def _append(args: list[str], *values: str) -> None:
        args.extend(values)

    @staticmethod
    def _under(path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except (ValueError, OSError):
            return False

    @staticmethod
    def _mask_path(args: list[str], path: Path) -> None:
        try:
            if not path.exists():
                return
            resolved = path.resolve()
            if resolved.is_dir():
                args.extend(["--tmpfs", str(resolved)])
            else:
                args.extend(["--ro-bind", "/dev/null", str(resolved)])
        except OSError:
            return

    def _sensitive_paths(self) -> tuple[Path, ...]:
        common = (
            ".ssh",
            ".gnupg",
            ".aws",
            ".azure",
            ".kube",
            ".docker",
            ".config/gcloud",
            ".config/gh",
            ".config/glab",
            ".local/share/keyrings",
            ".password-store",
            ".netrc",
            ".git-credentials",
            ".npmrc",
            ".pypirc",
            ".cargo/credentials",
            ".cargo/credentials.toml",
            ".m2/settings.xml",
            ".gradle/gradle.properties",
        )
        kitt_private = (
            ".kitt/security",
            ".kitt/config",
            ".kitt/state",
            ".kitt/workspaces",
            ".kitt/auth",
            ".kitt/credentials",
            ".kitt/memory",
            ".kitt/sessions",
        )
        items = list(kitt_private)
        if "credentials.read" not in self.permissions:
            items.extend(common)
        return tuple(self.home / item for item in items)

    def plan(
        self,
        base_argv: list[str],
        *,
        snapshot_root: Path,
        ipc_dir: Path,
        worker_home: Path,
        env: dict[str, str],
    ) -> PluginWorkerLaunchPlan:
        snapshot = snapshot_root.resolve()
        ipc = ipc_dir.resolve()
        ephemeral_home = worker_home.resolve()
        planned_env = dict(env)
        planned_env.update(
            {
                "HOME": str(ephemeral_home),
                "USERPROFILE": str(ephemeral_home),
                "XDG_CONFIG_HOME": str(ephemeral_home / ".config"),
                "XDG_CACHE_HOME": str(ephemeral_home / ".cache"),
                "XDG_DATA_HOME": str(ephemeral_home / ".local" / "share"),
            }
        )

        has_read = (
            "filesystem.read" in self.permissions
            or "filesystem.write" in self.permissions
        )
        has_write = "filesystem.write" in self.permissions
        has_network = "network" in self.permissions
        workspace_access = "write" if has_write else ("read" if has_read else "none")

        if sys.platform != "linux":
            return PluginWorkerLaunchPlan(
                argv=list(base_argv),
                env=planned_env,
                backend="process",
                strong=False,
                network_isolated=False,
                workspace_access=workspace_access,
                metadata={"reason": "os-process-isolation-only"},
            )

        bwrap = self._sandbox.bubblewrap_executable()
        if bwrap is None:
            return PluginWorkerLaunchPlan(
                argv=list(base_argv),
                env=planned_env,
                backend="process",
                strong=False,
                network_isolated=False,
                workspace_access=workspace_access,
                metadata={"reason": "bubblewrap-unavailable"},
            )

        args = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
        ]

        # Hide global runtime sockets. /tmp is made private unless one of the
        # paths that must be rebound lives below it.
        for hidden in (Path("/run"), Path("/var/tmp")):
            if hidden.is_dir():
                self._append(args, "--tmpfs", str(hidden))
        tmp_root = Path(tempfile.gettempdir()).resolve()
        needs_host_tmp = any(
            self._under(path, tmp_root)
            for path in (self.workspace_root, snapshot, ipc, ephemeral_home)
        )
        if Path("/tmp").is_dir() and not needs_host_tmp:
            self._append(args, "--tmpfs", "/tmp")

        # Always preserve only the exact immutable plugin snapshot and its
        # private broker channel as explicit mounts.
        self._append(args, "--ro-bind", str(snapshot), str(snapshot))
        self._append(args, "--ro-bind", str(ipc), str(ipc))
        self._append(
            args,
            "--bind",
            str(ephemeral_home),
            str(ephemeral_home),
        )

        if workspace_access == "write":
            self._append(
                args,
                "--bind",
                str(self.workspace_root),
                str(self.workspace_root),
            )
            for protected_name in (".git", ".kitt"):
                protected = self.workspace_root / protected_name
                if protected.exists():
                    self._append(
                        args,
                        "--ro-bind",
                        str(protected),
                        str(protected),
                    )
        elif workspace_access == "read":
            self._append(
                args,
                "--ro-bind",
                str(self.workspace_root),
                str(self.workspace_root),
            )
        else:
            self._append(args, "--tmpfs", str(self.workspace_root))

        for sensitive in self._sensitive_paths():
            if self._under(snapshot, sensitive):
                continue
            self._mask_path(args, sensitive)

        if not has_network:
            args.append("--unshare-net")

        args.extend(["--chdir", str(snapshot), "--", *base_argv])
        return PluginWorkerLaunchPlan(
            argv=args,
            env=planned_env,
            backend="bubblewrap",
            strong=True,
            network_isolated=not has_network,
            workspace_access=workspace_access,
            metadata={
                "reason": "permission-scoped-bubblewrap",
                "permissions": sorted(self.permissions),
            },
        )


def _jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return repr(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"__bytes_hex__": value[:65536].hex()}
    if is_dataclass(value):
        return _jsonable(asdict(value), depth + 1)
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item, depth + 1)
            for key, item in list(value.items())[:1000]
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item, depth + 1) for item in list(value)[:1000]]
    if hasattr(value, "__dict__"):
        payload = {
            str(key): _jsonable(item, depth + 1)
            for key, item in list(vars(value).items())[:200]
            if not str(key).startswith("_")
        }
        payload["__class__"] = type(value).__name__
        return payload
    return repr(value)


class PluginWorkerClient:
    def __init__(
        self,
        *,
        plugin_name: str,
        event_bus=None,
        config_api=None,
        timeout_seconds: float = 10.0,
        workspace_root: str | Path = ".",
        permissions: set[str] | list[str] | tuple[str, ...] = (),
        sandbox: PluginWorkerSandbox | None = None,
    ):
        self.plugin_name = plugin_name
        self.event_bus = event_bus
        self.config_api = config_api
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.permissions = frozenset(str(item) for item in permissions)
        self.sandbox = sandbox or PluginWorkerSandbox(
            self.workspace_root,
            self.permissions,
        )
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        self._server: socket.socket | None = None
        self._socket: socket.socket | None = None
        self._stream = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._closed = False
        self._ipc_dir: Path | None = None
        self.ipc_transport = "tcp"
        self.sandbox_metadata: dict[str, Any] = {
            "backend": "process",
            "strong": False,
            "network_isolated": False,
            "workspace_access": "none",
        }
        self.registrations: list[dict] = []

    def _send(self, payload: dict) -> None:
        if self._stream is None:
            raise RuntimeError("plugin worker IPC is not connected")
        encoded = (
            json.dumps(_jsonable(payload), ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_MESSAGE_BYTES:
            raise RuntimeError("plugin worker IPC request exceeds maximum size")
        self._stream.write(encoded)
        self._stream.flush()

    def _recv(self) -> dict:
        if self._stream is None:
            raise RuntimeError("plugin worker IPC is not connected")
        raw = self._stream.readline(_MAX_MESSAGE_BYTES + 1)
        if not raw:
            code = self._process.poll() if self._process is not None else None
            raise RuntimeError(
                f"plugin worker disconnected unexpectedly (exit={code})"
            )
        if len(raw) > _MAX_MESSAGE_BYTES:
            raise RuntimeError("plugin worker IPC response exceeds maximum size")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("plugin worker returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("plugin worker response must be an object")
        return payload

    def _apply_side_effects(self, payload: dict) -> None:
        for update in payload.get("config_updates", []) or []:
            if (
                self.config_api is not None
                and isinstance(update, dict)
                and isinstance(update.get("key"), str)
            ):
                self.config_api.set(update["key"], update.get("value"))

        if self.event_bus is not None:
            for event in payload.get("published_events", []) or []:
                if isinstance(event, dict) and isinstance(event.get("name"), str):
                    self.event_bus.publish(event["name"], event.get("payload"))

        for record in payload.get("logs", []) or []:
            if not isinstance(record, dict):
                continue
            message = str(record.get("message", ""))[:8000]
            level = str(record.get("level", "info")).lower()
            plugin_logger = logging.getLogger(f"kitt.plugin.{self.plugin_name}")
            log_fn = getattr(plugin_logger, level, plugin_logger.info)
            log_fn("%s", message)

    def _request(self, payload: dict) -> dict:
        with self._lock:
            if self._closed:
                raise RuntimeError("plugin worker is closed")
            self._send(payload)
            response = self._recv()
            self._apply_side_effects(response)
            if not response.get("ok", False):
                raise RuntimeError(
                    str(response.get("error") or "plugin worker request failed")
                )
            return response

    @staticmethod
    def _read_hello(conn: socket.socket) -> dict:
        stream = conn.makefile("rb")
        try:
            raw = stream.readline(_MAX_MESSAGE_BYTES + 1)
        finally:
            stream.close()
        if not raw or len(raw) > _MAX_MESSAGE_BYTES:
            raise RuntimeError("plugin worker handshake failed")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("plugin worker handshake is invalid")
        return payload

    def start(
        self,
        *,
        snapshot_root: Path,
        manifest: dict,
        config: dict,
    ) -> list[dict]:
        worker_main = Path(__file__).with_name("worker_main.py").resolve()
        if not worker_main.is_file():
            raise RuntimeError("plugin worker bootstrap is missing")

        ipc_base = ensure_private_dir(kitt_home() / "ipc")
        ipc_dir = Path(
            tempfile.mkdtemp(
                prefix=f"{re.sub(r'[^a-zA-Z0-9_.-]', '_', self.plugin_name)}-",
                dir=str(ipc_base),
            )
        ).resolve()
        if os.name != "nt":
            os.chmod(ipc_dir, 0o700)
        self._ipc_dir = ipc_dir
        worker_home = ipc_dir / "home"
        worker_home.mkdir(mode=0o700, exist_ok=True)

        token = secrets.token_hex(32)
        if os.name != "nt" and hasattr(socket, "AF_UNIX"):
            socket_path = ipc_dir / "broker.sock"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(socket_path))
            os.chmod(socket_path, 0o600)
            connection_args = ["--socket", str(socket_path)]
            self.ipc_transport = "unix"
        else:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            host, port = server.getsockname()
            connection_args = [
                "--host",
                str(host),
                "--port",
                str(port),
            ]
            self.ipc_transport = "tcp"

        server.listen(1)
        server.settimeout(self.timeout_seconds)
        base_argv = [
            sys.executable,
            "-I",
            "-u",
            str(worker_main),
            *connection_args,
            "--token",
            token,
        ]
        launch = self.sandbox.plan(
            base_argv,
            snapshot_root=snapshot_root,
            ipc_dir=ipc_dir,
            worker_home=worker_home,
            env=_sanitized_worker_env(),
        )
        self.sandbox_metadata = {
            "backend": launch.backend,
            "strong": launch.strong,
            "network_isolated": launch.network_isolated,
            "workspace_access": launch.workspace_access,
            **dict(launch.metadata),
        }

        self._server = server
        self._process = subprocess.Popen(
            launch.argv,
            cwd=str(snapshot_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            env=launch.env,
        )

        try:
            conn, _ = server.accept()
            conn.settimeout(self.timeout_seconds)
            hello = self._read_hello(conn)
            if (
                hello.get("type") != "hello"
                or not secrets.compare_digest(str(hello.get("token", "")), token)
            ):
                conn.close()
                raise RuntimeError("plugin worker authentication failed")
            self._socket = conn
            self._stream = conn.makefile("rwb", buffering=0)
            response = self._request(
                {
                    "op": "load",
                    "snapshot_root": str(snapshot_root),
                    "manifest": manifest,
                    "config": config,
                }
            )
            registrations = response.get("registrations", [])
            if not isinstance(registrations, list):
                raise RuntimeError("plugin worker registrations must be a list")
            self.registrations = [
                item for item in registrations if isinstance(item, dict)
            ]
            return list(self.registrations)
        except Exception:
            self.close()
            raise
        finally:
            try:
                server.close()
            except OSError:
                pass
            self._server = None

    def invoke(
        self,
        handler_id: str,
        args: list | tuple | None = None,
        kwargs: dict | None = None,
    ) -> Any:
        response = self._request(
            {
                "op": "invoke",
                "handler_id": str(handler_id),
                "args": list(args or []),
                "kwargs": dict(kwargs or {}),
            }
        )
        return response.get("result")

    def lifecycle(self, phase: str) -> Any:
        response = self._request({"op": "lifecycle", "phase": str(phase)})
        return response.get("result")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                if self._stream is not None and self._process is not None:
                    if self._process.poll() is None:
                        try:
                            self._send({"op": "shutdown"})
                            self._recv()
                        except Exception:
                            pass
            finally:
                self._closed = True
                try:
                    if self._stream is not None:
                        self._stream.close()
                except Exception:
                    pass
                try:
                    if self._socket is not None:
                        self._socket.close()
                except OSError:
                    pass
                process = self._process
                if process is not None and process.poll() is None:
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            process.kill()
                self._stream = None
                self._socket = None
                self._process = None
                ipc_dir = self._ipc_dir
                self._ipc_dir = None
                if ipc_dir is not None:
                    shutil.rmtree(ipc_dir, ignore_errors=True)
