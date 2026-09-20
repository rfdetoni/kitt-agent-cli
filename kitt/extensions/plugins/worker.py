"""Parent-side isolated plugin worker client using authenticated JSON IPC."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
from typing import Any


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
    ):
        self.plugin_name = plugin_name
        self.event_bus = event_bus
        self.config_api = config_api
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        self._server: socket.socket | None = None
        self._socket: socket.socket | None = None
        self._stream = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._closed = False
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

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(self.timeout_seconds)
        host, port = server.getsockname()
        token = secrets.token_hex(32)

        self._server = server
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-u",
                str(worker_main),
                "--host",
                str(host),
                "--port",
                str(port),
                "--token",
                token,
            ],
            cwd=str(snapshot_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            env=_sanitized_worker_env(),
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
