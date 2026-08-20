"""Domain models, states, and capabilities for KITT plugins and extensions."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import FrozenSet, List, Optional, Set

CURRENT_PLUGIN_API_VERSION = "1"

VALID_PERMISSIONS: FrozenSet[str] = frozenset({
    "events.read",
    "tools.observe",
    "tools.register",
    "tools.modify",
    "model.observe",
    "model.modify",
    "context.observe",
    "context.modify",
    "memory.read",
    "memory.write",
    "sessions.read",
    "filesystem.read",
    "filesystem.write",
    "network",
    "commands.register",
    "providers.register",
    "mcp.manage",
    "credentials.read",
})

SENSITIVE_PERMISSIONS: FrozenSet[str] = frozenset({
    "filesystem.write",
    "network",
    "credentials.read",
    "model.modify",
    "tools.modify",
    "mcp.manage",
})


class PluginState(Enum):
    DISCOVERED = "DISCOVERED"
    LOADING = "LOADING"
    LOADED = "LOADED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class PluginIdentity:
    name: str
    version: str
    source: str
    root_path: Optional[Path] = None


@dataclass
class PluginManifest:
    name: str
    version: str
    api_version: str
    entrypoint: str
    permissions: Set[str] = field(default_factory=set)
    description: str = ""
    author: str = ""
    homepage: str = ""
    dependencies: List[str] = field(default_factory=list)
    requires_kitt: str = ">=1.0.0"
    enabled_by_default: bool = True
    is_critical: bool = False
    source: str = "workspace"
    manifest_path: Optional[Path] = None
    trusted_in_process: bool = False
