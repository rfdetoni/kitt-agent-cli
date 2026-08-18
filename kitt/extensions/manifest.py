"""Manifest parser and schema validator using Python stdlib tomllib."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional

from kitt.extensions.errors import (
    PluginCompatibilityError,
    PluginManifestError,
    PluginPermissionError,
)
from kitt.extensions.models import (
    CURRENT_PLUGIN_API_VERSION,
    VALID_PERMISSIONS,
    PluginManifest,
)

MAX_MANIFEST_SIZE_BYTES = 64 * 1024  # 64 KB
PLUGIN_NAME_REGEX = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


def validate_plugin_name(name: str) -> None:
    if not name or not PLUGIN_NAME_REGEX.match(name.strip().lower()):
        raise PluginManifestError(
            f"Invalid plugin name '{name}'. Name must be 1-64 alphanumeric characters, dashes, or underscores, starting with alphanumeric."
        )
    if ".." in name or "/" in name or "\\" in name:
        raise PluginManifestError(f"Plugin name '{name}' contains illegal path traversal sequences.")


def parse_manifest_file(manifest_path: Path, source: str = "workspace") -> PluginManifest:
    """Parses and validates a plugin.toml manifest file."""
    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise PluginManifestError(f"Plugin manifest file not found: {path}")

    # Check bounds
    size = path.stat().st_size
    if size > MAX_MANIFEST_SIZE_BYTES:
        raise PluginManifestError(f"Plugin manifest {path} exceeds maximum size limit ({size} > {MAX_MANIFEST_SIZE_BYTES} bytes).")

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        raise PluginManifestError(f"Failed to parse TOML in {path}: {exc}") from exc

    return parse_manifest_data(data, manifest_path=path, source=source)


def parse_manifest_data(data: Dict[str, Any], manifest_path: Optional[Path] = None, source: str = "workspace") -> PluginManifest:
    """Validates dictionary data against PluginManifest schema."""
    name = str(data.get("name", "")).strip()
    if not name:
        raise PluginManifestError("Plugin manifest missing required 'name' field.")
    validate_plugin_name(name)

    version = str(data.get("version", "")).strip()
    if not version:
        raise PluginManifestError(f"Plugin '{name}' manifest missing required 'version' field.")

    api_version = str(data.get("api_version", data.get("apiVersion", ""))).strip()
    if not api_version:
        raise PluginManifestError(f"Plugin '{name}' manifest missing required 'api_version' field.")
    if api_version != CURRENT_PLUGIN_API_VERSION:
        raise PluginCompatibilityError(
            f"Plugin '{name}' requires API version {api_version}, but KITT supports version {CURRENT_PLUGIN_API_VERSION}."
        )

    entrypoint = str(data.get("entrypoint", "")).strip()
    if not entrypoint:
        raise PluginManifestError(f"Plugin '{name}' manifest missing required 'entrypoint' field.")

    # Permissions
    raw_perms = data.get("permissions", [])
    if not isinstance(raw_perms, list):
        raise PluginManifestError(f"Plugin '{name}' permissions must be a list of strings.")

    permissions = set()
    for p in raw_perms:
        p_str = str(p).strip()
        if p_str not in VALID_PERMISSIONS:
            raise PluginPermissionError(f"Plugin '{name}' requests unknown permission '{p_str}'.")
        permissions.add(p_str)

    description = str(data.get("description", ""))
    author = str(data.get("author", ""))
    homepage = str(data.get("homepage", ""))
    dependencies = [str(d).strip() for d in data.get("dependencies", []) if isinstance(d, (str, int, float))]
    requires_kitt = str(data.get("requires_kitt", ">=1.0.0"))
    enabled_by_default = bool(data.get("enabled_by_default", True))
    is_critical = bool(data.get("is_critical", False))

    return PluginManifest(
        name=name,
        version=version,
        api_version=api_version,
        entrypoint=entrypoint,
        permissions=permissions,
        description=description,
        author=author,
        homepage=homepage,
        dependencies=dependencies,
        requires_kitt=requires_kitt,
        enabled_by_default=enabled_by_default,
        is_critical=is_critical,
        source=source,
        manifest_path=manifest_path,
    )
