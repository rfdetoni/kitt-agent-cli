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

MAX_MANIFEST_SIZE_BYTES = 64 * 1024
PLUGIN_NAME_REGEX = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


def validate_plugin_name(name: str) -> None:
    if not name or not PLUGIN_NAME_REGEX.match(name.strip().lower()):
        raise PluginManifestError(
            f"Invalid plugin name '{name}'. Name must be 1-64 alphanumeric "
            "characters, dashes, or underscores, starting with alphanumeric."
        )
    if ".." in name or "/" in name or "\\" in name:
        raise PluginManifestError(
            f"Plugin name '{name}' contains illegal path traversal sequences."
        )


def parse_manifest_file(
    manifest_path: Path,
    source: str = "workspace",
) -> PluginManifest:
    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise PluginManifestError(f"Plugin manifest file not found: {path}")

    size = path.stat().st_size
    if size > MAX_MANIFEST_SIZE_BYTES:
        raise PluginManifestError(
            f"Plugin manifest {path} exceeds maximum size limit "
            f"({size} > {MAX_MANIFEST_SIZE_BYTES} bytes)."
        )

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except Exception as exc:
        raise PluginManifestError(f"Failed to parse TOML in {path}: {exc}") from exc

    return parse_manifest_data(data, manifest_path=path, source=source)


def parse_manifest_data(
    data: Dict[str, Any],
    manifest_path: Optional[Path] = None,
    source: str = "workspace",
) -> PluginManifest:
    name = str(data.get("name", "")).strip().lower()
    if not name:
        raise PluginManifestError("Plugin manifest missing required 'name' field.")
    validate_plugin_name(name)

    version = str(data.get("version", "")).strip()
    if not version:
        raise PluginManifestError(
            f"Plugin '{name}' manifest missing required 'version' field."
        )

    api_version = str(data.get("api_version", data.get("apiVersion", ""))).strip()
    if not api_version:
        raise PluginManifestError(
            f"Plugin '{name}' manifest missing required 'api_version' field."
        )
    if api_version != CURRENT_PLUGIN_API_VERSION:
        raise PluginCompatibilityError(
            f"Plugin '{name}' requires API version {api_version}, but KITT "
            f"supports version {CURRENT_PLUGIN_API_VERSION}."
        )

    entrypoint = str(data.get("entrypoint", "")).strip()
    if not entrypoint:
        raise PluginManifestError(
            f"Plugin '{name}' manifest missing required 'entrypoint' field."
        )

    raw_permissions = data.get("permissions", [])
    if not isinstance(raw_permissions, list):
        raise PluginManifestError(
            f"Plugin '{name}' permissions must be a list of strings."
        )

    permissions = set()
    for permission in raw_permissions:
        permission_name = str(permission).strip()
        if permission_name not in VALID_PERMISSIONS:
            raise PluginPermissionError(
                f"Plugin '{name}' requests unknown permission '{permission_name}'."
            )
        permissions.add(permission_name)

    dependencies = [
        str(item).strip()
        for item in data.get("dependencies", [])
        if isinstance(item, (str, int, float))
    ]
    trusted_raw = data.get("trusted_in_process", False)
    if not isinstance(trusted_raw, bool):
        raise PluginManifestError(
            f"Plugin '{name}' trusted_in_process must be boolean."
        )
    enabled_raw = data.get("enabled_by_default", True)
    if not isinstance(enabled_raw, bool):
        raise PluginManifestError(
            f"Plugin '{name}' enabled_by_default must be boolean."
        )
    critical_raw = data.get("is_critical", False)
    if not isinstance(critical_raw, bool):
        raise PluginManifestError(
            f"Plugin '{name}' is_critical must be boolean."
        )

    return PluginManifest(
        name=name,
        version=version,
        api_version=api_version,
        entrypoint=entrypoint,
        permissions=permissions,
        description=str(data.get("description", "")),
        author=str(data.get("author", "")),
        homepage=str(data.get("homepage", "")),
        dependencies=dependencies,
        requires_kitt=str(data.get("requires_kitt", ">=1.0.0")),
        enabled_by_default=enabled_raw,
        is_critical=critical_raw,
        source=source,
        manifest_path=manifest_path,
        trusted_in_process=trusted_raw,
    )
