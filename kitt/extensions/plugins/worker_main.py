"""Standalone isolated plugin worker. Stdlib only by design."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, is_dataclass
import importlib.util
import inspect
import json
from pathlib import Path
import socket
import sys
import types
from typing import Any


_MAX_MESSAGE_BYTES = 4 * 1024 * 1024
_callbacks: dict[str, Any] = {}
_callback_meta: dict[str, dict] = {}
_registrations: list[dict] = []
_side_effects = {
    "config_updates": [],
    "published_events": [],
    "logs": [],
}
_lifecycle: dict[str, Any] = {"start": None, "stop": None}
_next_handler = 0


def _jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return repr(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"__bytes_hex__": value[:65536].hex()}
    if type(value).__name__ == "HookResult" and hasattr(value, "value"):
        return {
            "__kitt_type__": "HookResult",
            "value": _jsonable(value.value, depth + 1),
            "stop": bool(getattr(value, "stop", False)),
        }
    if is_dataclass(value):
        return _jsonable(asdict(value), depth + 1)
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item, depth + 1)
            for key, item in list(value.items())[:1000]
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item, depth + 1) for item in list(value)[:1000]]
    return repr(value)


def _reset_side_effects() -> None:
    for value in _side_effects.values():
        value.clear()


def _response(ok: bool, **payload: Any) -> dict:
    return {
        "ok": ok,
        **payload,
        "config_updates": list(_side_effects["config_updates"]),
        "published_events": list(_side_effects["published_events"]),
        "logs": list(_side_effects["logs"]),
    }


def _register(kind: str, name: str, handler: Any, **metadata: Any) -> str:
    global _next_handler
    if not callable(handler) and not callable(getattr(handler, "execute", None)):
        raise TypeError(f"registered {kind} handler must be callable")
    _next_handler += 1
    handler_id = f"h{_next_handler}"
    _callbacks[handler_id] = handler
    _callback_meta[handler_id] = {"kind": kind, "name": name}
    _registrations.append(
        {
            "kind": kind,
            "name": name,
            "handler_id": handler_id,
            **_jsonable(metadata),
        }
    )
    return handler_id


class _Logger:
    def __init__(self, plugin_name: str):
        self.plugin_name = plugin_name

    def _log(self, level: str, message: Any, *args: Any) -> None:
        text = str(message)
        if args:
            try:
                text = text % args
            except Exception:
                text = " ".join([text, *map(str, args)])
        _side_effects["logs"].append(
            {"level": level, "message": text[:8000]}
        )

    def debug(self, message: Any, *args: Any) -> None:
        self._log("debug", message, *args)

    def info(self, message: Any, *args: Any) -> None:
        self._log("info", message, *args)

    def warning(self, message: Any, *args: Any) -> None:
        self._log("warning", message, *args)

    def error(self, message: Any, *args: Any) -> None:
        self._log("error", message, *args)


class _Config:
    def __init__(self, initial: dict):
        self._data = dict(initial or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[str(key)] = value
        _side_effects["config_updates"].append(
            {"key": str(key), "value": _jsonable(value)}
        )

    def save(self) -> None:
        return None


class _Events:
    def __init__(self, plugin_name: str, permissions: set[str]):
        self.plugin_name = plugin_name
        self.permissions = permissions

    def subscribe(self, event_name: str, handler: Any) -> None:
        if "events.read" not in self.permissions:
            raise PermissionError("events.read permission required")
        _register("event", str(event_name), handler)

    def publish(self, event_name: str, payload: Any) -> None:
        if "events.read" not in self.permissions:
            raise PermissionError("events.read permission required")
        _side_effects["published_events"].append(
            {"name": str(event_name), "payload": _jsonable(payload)}
        )


class _Hooks:
    def __init__(self, plugin_name: str, permissions: set[str]):
        self.plugin_name = plugin_name
        self.permissions = permissions

    def register(
        self,
        hook_name: str,
        handler: Any,
        *,
        priority: int = 0,
        fail_closed: bool = False,
        timeout_seconds: float | None = None,
    ) -> None:
        hook = str(hook_name)
        if (
            hook.startswith("tool.")
            and "tools.observe" not in self.permissions
            and "tools.modify" not in self.permissions
        ):
            raise PermissionError("tools.observe or tools.modify permission required")
        if (
            hook.startswith("model.")
            and "model.observe" not in self.permissions
            and "model.modify" not in self.permissions
        ):
            raise PermissionError("model.observe or model.modify permission required")
        if (
            hook.startswith("context.")
            and "context.observe" not in self.permissions
            and "context.modify" not in self.permissions
        ):
            raise PermissionError("context.observe or context.modify permission required")
        if (
            hook.startswith("memory.")
            and "memory.read" not in self.permissions
            and "memory.write" not in self.permissions
        ):
            raise PermissionError("memory.read or memory.write permission required")
        _register(
            "hook",
            hook,
            handler,
            priority=int(priority),
            fail_closed=bool(fail_closed),
            timeout_seconds=timeout_seconds,
        )


class _Tools:
    def __init__(self, plugin_name: str, permissions: set[str]):
        self.plugin_name = plugin_name
        self.permissions = permissions

    def register(
        self,
        tool_name: str,
        handler: Any,
        description: str = "",
        schema: dict | None = None,
    ) -> None:
        if "tools.register" not in self.permissions:
            raise PermissionError("tools.register permission required")
        _register(
            "tool",
            str(tool_name),
            handler,
            description=str(description or ""),
            schema=_jsonable(schema or {}),
        )


class _Commands:
    def __init__(self, plugin_name: str, permissions: set[str]):
        self.plugin_name = plugin_name
        self.permissions = permissions

    def register(
        self,
        command_name: str,
        handler: Any,
        help_text: str = "",
    ) -> None:
        if "commands.register" not in self.permissions:
            raise PermissionError("commands.register permission required")
        name = "/" + str(command_name).lstrip("/")
        _register(
            "command",
            name,
            handler,
            help_text=str(help_text or ""),
        )


class _Context:
    def __init__(self, manifest: dict, config: dict):
        permissions = set(manifest.get("permissions") or [])
        name = str(manifest.get("name") or "plugin")
        self.identity = types.SimpleNamespace(
            name=name,
            version=str(manifest.get("version") or ""),
            source=str(manifest.get("source") or ""),
            root_path=Path(str(manifest.get("root_path") or ".")),
        )
        self.manifest = types.SimpleNamespace(
            **{
                **manifest,
                "permissions": permissions,
            }
        )
        self.events = _Events(name, permissions)
        self.hooks = _Hooks(name, permissions)
        self.tools = _Tools(name, permissions)
        self.commands = _Commands(name, permissions)
        self.config = _Config(config)
        self.logger = _Logger(name)


def _ensure_package(module_name: str, package_path: Path) -> None:
    if module_name in sys.modules:
        return
    module = types.ModuleType(module_name)
    module.__path__ = [str(package_path)]
    module.__package__ = module_name
    module.__file__ = str(package_path / "__init__.py")
    sys.modules[module_name] = module


def _resolve_entrypoint(snapshot_root: Path, module_name: str):
    normalized = module_name.replace("\\", "/").strip("/")
    if normalized.endswith(".py"):
        normalized = normalized[:-3]
    if "/" not in normalized and "." in normalized:
        normalized = normalized.replace(".", "/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise RuntimeError(f"invalid plugin entrypoint module: {module_name}")
    module_candidate = snapshot_root.joinpath(*parts)
    py_file = module_candidate.with_suffix(".py")
    package_init = module_candidate / "__init__.py"
    if py_file.is_file():
        return parts, py_file, False
    if package_init.is_file():
        return parts, package_init, True
    if module_candidate.is_file():
        return parts, module_candidate, False
    raise RuntimeError(f"plugin entrypoint not found: {module_name}")


def _await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def _load(payload: dict) -> dict:
    _reset_side_effects()
    _registrations.clear()
    _callbacks.clear()
    _callback_meta.clear()
    _lifecycle["start"] = None
    _lifecycle["stop"] = None

    snapshot_root = Path(str(payload.get("snapshot_root") or "")).resolve(strict=True)
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        raise RuntimeError("manifest payload is invalid")
    manifest = dict(manifest)
    manifest["root_path"] = str(snapshot_root)
    entrypoint = str(manifest.get("entrypoint") or "")
    module_name, separator, function_name = entrypoint.partition(":")
    function_name = function_name if separator else "setup"

    package_key = (
        "kitt_worker_plugin_"
        + "".join(
            char if char.isalnum() or char == "_" else "_"
            for char in str(manifest.get("name") or "plugin")
        )
    )
    parts, module_file, is_package = _resolve_entrypoint(snapshot_root, module_name)
    _ensure_package(package_key, snapshot_root)
    for depth in range(1, len(parts)):
        name = package_key + "." + ".".join(parts[:depth])
        _ensure_package(name, snapshot_root.joinpath(*parts[:depth]))

    module_key = package_key + "." + ".".join(parts)
    kwargs = {}
    if is_package:
        kwargs["submodule_search_locations"] = [str(module_file.parent)]
    spec = importlib.util.spec_from_file_location(module_key, module_file, **kwargs)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed creating plugin module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)

    setup = getattr(module, function_name, None)
    if not callable(setup):
        raise RuntimeError(f"plugin entrypoint is not callable: {function_name}")
    result = _await(setup(_Context(manifest, payload.get("config") or {})))

    if hasattr(result, "start") and hasattr(result, "stop"):
        _lifecycle["start"] = getattr(result, "start")
        _lifecycle["stop"] = getattr(result, "stop")
    elif isinstance(result, tuple) and len(result) == 2:
        _lifecycle["start"], _lifecycle["stop"] = result

    return _response(
        True,
        registrations=list(_registrations),
        execution_mode="worker",
    )


def _call_handler(handler: Any, args: list, kwargs: dict, kind: str) -> Any:
    target = handler
    if not callable(target) and callable(getattr(target, "execute", None)):
        target = target.execute
    try:
        signature = inspect.signature(target)
        positional = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        has_varargs = any(
            parameter.kind == inspect.Parameter.VAR_POSITIONAL
            for parameter in signature.parameters.values()
        )
        call_args = list(args)
        if not has_varargs and len(call_args) > len(positional):
            call_args = call_args[: len(positional)]
    except Exception:
        call_args = list(args)

    if kind == "hook" and len(call_args) >= 2 and isinstance(call_args[1], dict):
        call_args[1] = types.SimpleNamespace(**call_args[1])

    return _await(target(*call_args, **kwargs))


def _invoke(payload: dict) -> dict:
    _reset_side_effects()
    handler_id = str(payload.get("handler_id") or "")
    handler = _callbacks.get(handler_id)
    if handler is None:
        raise RuntimeError(f"unknown plugin handler: {handler_id}")
    meta = _callback_meta.get(handler_id, {})
    args = payload.get("args") or []
    kwargs = payload.get("kwargs") or {}
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise RuntimeError("plugin callback arguments are invalid")
    result = _call_handler(handler, args, kwargs, str(meta.get("kind") or ""))
    return _response(True, result=_jsonable(result))


def _lifecycle_call(payload: dict) -> dict:
    _reset_side_effects()
    phase = str(payload.get("phase") or "")
    if phase not in {"start", "stop"}:
        raise RuntimeError("invalid lifecycle phase")
    handler = _lifecycle.get(phase)
    result = None if handler is None else _await(handler())
    return _response(True, result=_jsonable(result))


def _send(stream, payload: dict) -> None:
    encoded = (
        json.dumps(_jsonable(payload), ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(encoded) > _MAX_MESSAGE_BYTES:
        raise RuntimeError("plugin worker response exceeds maximum size")
    stream.write(encoded)
    stream.flush()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    sock = socket.create_connection((args.host, args.port), timeout=10.0)
    stream = sock.makefile("rwb", buffering=0)
    _send(stream, {"type": "hello", "token": args.token})

    while True:
        raw = stream.readline(_MAX_MESSAGE_BYTES + 1)
        if not raw:
            break
        if len(raw) > _MAX_MESSAGE_BYTES:
            _send(stream, {"ok": False, "error": "request exceeds maximum size"})
            continue
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("request must be an object")
            op = str(payload.get("op") or "")
            if op == "load":
                response = _load(payload)
            elif op == "invoke":
                response = _invoke(payload)
            elif op == "lifecycle":
                response = _lifecycle_call(payload)
            elif op == "shutdown":
                _reset_side_effects()
                _send(stream, _response(True, result=None))
                break
            else:
                raise RuntimeError(f"unknown worker operation: {op}")
        except BaseException as exc:
            response = _response(
                False,
                error=f"{type(exc).__name__}: {exc}",
            )
        _send(stream, response)

    try:
        stream.close()
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
