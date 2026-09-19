"""
Kitt Agent CLI — autonomous coding agent control plane.

The KITT namespace is intentionally split across independently versioned
packages (agent-cli, assistant runtime, toolbox/native, evolution/evals).
"""

from importlib.metadata import PackageNotFoundError, version
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

try:
    __version__ = version("kitt-agent-cli")
except PackageNotFoundError:
    # Source-tree fallback for direct execution before installation.
    __version__ = "0.56.0"

KITT_VERSION = __version__
STATE_SCHEMA_VERSION = 2
DAEMON_PROTOCOL_VERSION = 1
NATIVE_PROTOCOL_VERSION = 1
