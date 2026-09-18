"""
Kitt Agent CLI — autonomous coding agent control plane.

The KITT namespace is intentionally split across independently versioned
packages (agent-cli, assistant runtime, toolbox/native, evolution/evals).
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

__version__ = "0.40.22"
KITT_VERSION = "0.40.22"
STATE_SCHEMA_VERSION = 1
DAEMON_PROTOCOL_VERSION = 1
NATIVE_PROTOCOL_VERSION = 1


def _install_progress_aware_completion_guard() -> None:
    # Patch the internal completion-guard entry point before runtime composition
    # imports it. This keeps existing imports/tests compatible while the runtime
    # uses result-aware exploration loop detection.
    from kitt.core import completion_guard as completion_guard
    from kitt.core.progress_guard import install_completion_guard

    completion_guard.install_completion_guard = install_completion_guard


_install_progress_aware_completion_guard()
