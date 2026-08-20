"""KITT Extension subsystem package (Plugins, Hooks, MCP)."""
from kitt.extensions.errors import (
    ExtensionError,
    ExtensionStartupFailed,
    HookReentrancyError,
    HookTimeoutError,
    MCPError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPTransportError,
    PluginCompatibilityError,
    PluginDependencyError,
    PluginLoadError,
    PluginManifestError,
    PluginPermissionError,
)
from kitt.extensions.hooks.models import HookContext, HookRegistration, HookResult
from kitt.extensions.hooks.registry import HookRegistry
from kitt.extensions.manager import ExtensionManager
from kitt.extensions.manifest import parse_manifest_data, parse_manifest_file
from kitt.extensions.mcp.client import MCPClient
from kitt.extensions.mcp.manager import MCPManager
from kitt.extensions.mcp.models import (
    MCPPrompt,
    MCPResource,
    MCPServerConfig,
    MCPServerState,
    MCPTool,
)
from kitt.extensions.mcp.transport import HTTPTransport
from kitt.extensions.models import (
    CURRENT_PLUGIN_API_VERSION,
    VALID_PERMISSIONS,
    PluginIdentity,
    PluginManifest,
    PluginState,
)
from kitt.extensions.plugins.context import PluginContext
from kitt.extensions.plugins.loader import PluginHandle, PluginInstance, PluginLoader
from kitt.extensions.plugins.registry import PluginRegistry

__all__ = [
    "ExtensionManager",
    "PluginManifest",
    "PluginIdentity",
    "PluginState",
    "PluginContext",
    "PluginHandle",
    "PluginInstance",
    "PluginLoader",
    "PluginRegistry",
    "HookRegistry",
    "HookContext",
    "HookResult",
    "HookRegistration",
    "MCPManager",
    "MCPClient",
    "MCPTool",
    "MCPResource",
    "MCPPrompt",
    "HTTPTransport",
    "MCPServerConfig",
    "MCPServerState",
    "ExtensionError",
    "ExtensionStartupFailed",
    "PluginManifestError",
    "PluginCompatibilityError",
    "PluginPermissionError",
    "PluginDependencyError",
    "PluginLoadError",
    "HookTimeoutError",
    "HookReentrancyError",
    "MCPError",
    "MCPTransportError",
    "MCPProtocolError",
    "MCPTimeoutError",
    "CURRENT_PLUGIN_API_VERSION",
    "VALID_PERMISSIONS",
    "parse_manifest_file",
    "parse_manifest_data",
]
