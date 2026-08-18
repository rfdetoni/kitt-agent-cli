"""Error hierarchy for KITT extensions, plugins, hooks, and MCP subsystem."""
from __future__ import annotations


class ExtensionError(Exception):
    """Base exception for all extension errors."""


class PluginManifestError(ExtensionError):
    """Raised when a plugin manifest is missing, malformed, or invalid."""


class PluginCompatibilityError(ExtensionError):
    """Raised when a plugin API version is incompatible with KITT."""


class PluginPermissionError(ExtensionError):
    """Raised when a plugin attempts to access an unauthorized capability."""


class PluginDependencyError(ExtensionError):
    """Raised when a plugin dependency is missing."""


class PluginLoadError(ExtensionError):
    """Raised when importing or executing plugin setup fails."""


class HookTimeoutError(ExtensionError):
    """Raised when a hook interceptor exceeds its execution timeout."""


class HookReentrancyError(ExtensionError):
    """Raised when a recursive hook invocation loop is detected."""


class MCPError(ExtensionError):
    """Base exception for Model Context Protocol errors."""


class MCPTransportError(MCPError):
    """Raised when MCP transport connection or communication fails."""


class MCPProtocolError(MCPError):
    """Raised when MCP JSON-RPC message is malformed or invalid."""


class MCPTimeoutError(MCPError):
    """Raised when MCP call exceeds timeout."""
