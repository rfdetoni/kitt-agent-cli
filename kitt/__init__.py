"""
Kitt Agent CLI — autonomous coding agent control plane.

The KITT namespace is intentionally split across independently versioned
packages (agent-cli, assistant runtime, toolbox/native, evolution/evals).
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

__version__ = "0.46.1"
KITT_VERSION = "0.46.1"
STATE_SCHEMA_VERSION = 1
DAEMON_PROTOCOL_VERSION = 1
NATIVE_PROTOCOL_VERSION = 1
