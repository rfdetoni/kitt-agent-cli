"""Global-first verification contracts with safe project overrides.

The contract never accepts arbitrary commands from a workspace. It can only
enable/disable or bound timeout for verification step IDs that KITT already
knows how to construct deterministically.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from kitt.security.private_state import (
    InterProcessFileLock,
    kitt_home,
    secure_read_json,
    secure_write_json,
)

VERIFICATION_CONTRACT_VERSION = 1
GLOBAL_VERIFICATION_VERSION = 1
PROJECT_CONTRACT_RELATIVE_PATH = ".kitt/verification.json"
GLOBAL_BASELINES_DISPLAY_PATH = "~/.kitt/verification/baselines.json"
_MAX_PROJECT_BYTES = 128 * 1024

_BUILTIN_STEPS: dict[str, dict[str, Any]] = {
    "python.targeted-tests": {"enabled": True, "timeout_seconds": 120},
    "python.lint": {"enabled": True, "timeout_seconds": 90},
    "python.tests": {"enabled": True, "timeout_seconds": 180},
    "java.compile": {"enabled": True, "timeout_seconds": 180},
    "java.tests": {"enabled": True, "timeout_seconds": 240},
    "jvm.compile": {"enabled": True, "timeout_seconds": 180},
    "jvm.check": {"enabled": True, "timeout_seconds": 240},
    "node.typecheck": {"enabled": True, "timeout_seconds": 180},
    "node.lint": {"enabled": True, "timeout_seconds": 180},
    "node.test": {"enabled": True, "timeout_seconds": 240},
    "go.vet": {"enabled": True, "timeout_seconds": 180},
    "go.tests": {"enabled": True, "timeout_seconds": 240},
    "rust.check": {"enabled": True, "timeout_seconds": 180},
    "rust.tests": {"enabled": True, "timeout_seconds": 240},
    "dotnet.build": {"enabled": True, "timeout_seconds": 240},
    "dotnet.tests": {"enabled": True, "timeout_seconds": 240},
    "swift.build": {"enabled": True, "timeout_seconds": 240},
    "swift.tests": {"enabled": True, "timeout_seconds": 240},
    "dart.analyze": {"enabled": True, "timeout_seconds": 180},
    "dart.tests": {"enabled": True, "timeout_seconds": 240},
    "ruby.tests": {"enabled": True, "timeout_seconds": 240},
    "php.lint": {"enabled": True, "timeout_seconds": 180},
    "php.test": {"enabled": True, "timeout_seconds": 240},
}


def _builtin_document() -> dict[str, Any]:
    return {
        "version": GLOBAL_VERIFICATION_VERSION,
        "managed_by": "kitt-agent-cli",
        "max_steps": 12,
        "steps": {key: dict(value) for key, value in _BUILTIN_STEPS.items()},
    }


def _sanitize_step_override(name: str, raw: Any) -> dict[str, Any]:
    if name not in _BUILTIN_STEPS or not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    if "enabled" in raw:
        result["enabled"] = bool(raw["enabled"])
    if "timeout_seconds" in raw:
        try:
            timeout = int(raw["timeout_seconds"])
        except (TypeError, ValueError):
            timeout = _BUILTIN_STEPS[name]["timeout_seconds"]
        result["timeout_seconds"] = max(5, min(timeout, 600))
    return result


def _sanitize_document(raw: Any, *, project: bool) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    if "max_steps" in raw:
        try:
            result["max_steps"] = max(1, min(int(raw["max_steps"]), 12))
        except (TypeError, ValueError):
            pass
    steps = raw.get("steps")
    if isinstance(steps, dict):
        sanitized: dict[str, Any] = {}
        for name, value in steps.items():
            override = _sanitize_step_override(str(name), value)
            if override:
                sanitized[str(name)] = override
        if sanitized:
            result["steps"] = sanitized
    if project:
        result["version"] = VERIFICATION_CONTRACT_VERSION
        result["baseline"] = {
            "scope": "user-global",
            "version": GLOBAL_VERIFICATION_VERSION,
            "path": GLOBAL_BASELINES_DISPLAY_PATH,
        }
    return result


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = {
        "version": base.get("version", GLOBAL_VERIFICATION_VERSION),
        "managed_by": base.get("managed_by", "kitt-agent-cli"),
        "max_steps": base.get("max_steps", 12),
        "steps": {name: dict(value) for name, value in (base.get("steps") or {}).items()},
    }
    if "max_steps" in override:
        result["max_steps"] = override["max_steps"]
    for name, value in (override.get("steps") or {}).items():
        if name in result["steps"]:
            result["steps"][name].update(value)
    if "baseline" in override:
        result["baseline"] = override["baseline"]
    return result


class VerificationContractManager:
    """Resolve trusted global baselines plus constrained project overrides."""

    def __init__(self, root: str | Path, *, global_path: Path | None = None):
        self.root = Path(root).resolve()
        self.project_path = self.root / PROJECT_CONTRACT_RELATIVE_PATH
        self.global_path = global_path or (kitt_home() / "verification" / "baselines.json")
        self._cached: dict[str, Any] | None = None

    def _ensure_global(self) -> dict[str, Any]:
        lock_path = self.global_path.with_suffix(self.global_path.suffix + ".lock")
        with InterProcessFileLock(lock_path):
            existing = secure_read_json(self.global_path, default=None)
            builtin = _builtin_document()
            sanitized = _sanitize_document(existing, project=False)
            merged = _merge(builtin, sanitized)
            if existing != merged:
                secure_write_json(self.global_path, merged)
            return merged

    def _read_project(self) -> dict[str, Any]:
        path = self.project_path
        try:
            stat = path.lstat()
        except FileNotFoundError:
            return {}
        except OSError:
            return {}
        if path.is_symlink() or not path.is_file() or stat.st_size > _MAX_PROJECT_BYTES:
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        version = int(raw.get("version", VERIFICATION_CONTRACT_VERSION) or 0)
        if version != VERIFICATION_CONTRACT_VERSION:
            return {}
        return _sanitize_document(raw, project=True)

    def effective(self) -> dict[str, Any]:
        global_doc = self._ensure_global()
        project = self._read_project()
        self._cached = _merge(global_doc, project)
        return self._cached

    def apply_plan(self, steps: Iterable[Any]) -> list[Any]:
        contract = self.effective()
        configs = contract.get("steps") or {}
        max_steps = int(contract.get("max_steps", 12) or 12)
        result: list[Any] = []
        for step in steps:
            name = str(getattr(step, "name", "") or "")
            if name not in _BUILTIN_STEPS:
                continue
            config = configs.get(name) if isinstance(configs, dict) else None
            if not isinstance(config, dict):
                config = _BUILTIN_STEPS[name]
            if config.get("enabled", True) is not True:
                continue
            timeout = max(
                5,
                min(
                    int(config.get("timeout_seconds", getattr(step, "timeout_seconds", 120)) or 120),
                    600,
                ),
            )
            result.append(replace(step, timeout_seconds=timeout))
            if len(result) >= max_steps:
                break
        return result
