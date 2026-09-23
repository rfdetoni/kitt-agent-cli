"""Persistent, user-controlled runtime feature flags.

Flags are stored outside workspaces so one KITT preference applies consistently
across projects. Environment variables remain only as backward-compatible
fallbacks until a value is changed from inside KITT.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kitt.settings.control_center import config_root

_SCHEMA_VERSION = 1
VERIFY_FULL_FLAG = "KITT_AGENT_VERIFY_FULL"
_ALLOWED_FLAGS = frozenset({VERIFY_FULL_FLAG})
_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}


def runtime_flags_path() -> Path:
    return config_root() / "agent" / "runtime-flags.json"


def _empty() -> dict[str, Any]:
    return {"schema_version": _SCHEMA_VERSION, "flags": {}}


def load_runtime_flags(path: Path | None = None) -> dict[str, Any]:
    target = path or runtime_flags_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return _empty()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _empty()
    if not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION:
        return _empty()
    flags = data.get("flags")
    if not isinstance(flags, dict):
        return _empty()
    return {
        "schema_version": _SCHEMA_VERSION,
        "flags": {key: bool(value) for key, value in flags.items() if key in _ALLOWED_FLAGS},
    }


def get_runtime_flag(name: str, default: bool = False, *, path: Path | None = None) -> bool:
    if name not in _ALLOWED_FLAGS:
        raise ValueError(f"Unsupported KITT runtime flag: {name}")
    flags = load_runtime_flags(path)["flags"]
    if name in flags:
        return bool(flags[name])
    raw = os.getenv(name)
    if raw is not None:
        normalized = raw.strip().casefold()
        if normalized in _TRUE:
            return True
        if normalized in _FALSE:
            return False
    return bool(default)


def set_runtime_flag(name: str, enabled: bool, *, path: Path | None = None) -> bool:
    if name not in _ALLOWED_FLAGS:
        raise ValueError(f"Unsupported KITT runtime flag: {name}")
    target = path or runtime_flags_path()
    data = load_runtime_flags(target)
    flags = dict(data.get("flags") or {})
    flags[name] = bool(enabled)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(
        json.dumps({"schema_version": _SCHEMA_VERSION, "flags": flags}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, target)
    return bool(enabled)


def verification_full_enabled(default: bool = False) -> bool:
    return get_runtime_flag(VERIFY_FULL_FLAG, default)


def set_verification_full_enabled(enabled: bool) -> bool:
    return set_runtime_flag(VERIFY_FULL_FLAG, enabled)
