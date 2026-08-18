"""PluginContext providing restricted capability APIs to plugins."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from kitt.extensions.models import PluginIdentity, PluginManifest
from kitt.extensions.plugins.api import (
    CommandAPI,
    EventAPI,
    HookAPI,
    PluginConfigAPI,
    PluginLogger,
    ToolAPI,
)


@dataclass
class PluginContext:
    """Restricted capability context passed to plugin entrypoints."""
    identity: PluginIdentity
    manifest: PluginManifest
    events: EventAPI
    hooks: HookAPI
    tools: ToolAPI
    commands: CommandAPI
    config: PluginConfigAPI
    logger: PluginLogger
