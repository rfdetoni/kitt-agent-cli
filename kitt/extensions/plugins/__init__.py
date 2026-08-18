"""Plugin system package."""
from kitt.extensions.plugins.api import (
    CommandAPI,
    EventAPI,
    HookAPI,
    PluginConfigAPI,
    PluginLogger,
    ToolAPI,
)
from kitt.extensions.plugins.context import PluginContext
from kitt.extensions.plugins.loader import PluginHandle, PluginInstance, PluginLoader
from kitt.extensions.plugins.registry import PluginRegistry

__all__ = [
    "PluginContext",
    "PluginHandle",
    "PluginInstance",
    "PluginLoader",
    "PluginRegistry",
    "EventAPI",
    "HookAPI",
    "ToolAPI",
    "CommandAPI",
    "PluginConfigAPI",
    "PluginLogger",
]
