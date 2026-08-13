from __future__ import annotations

import os
import sys

from kitt.ui.fallback import PlainLineUI
from kitt.ui.terminal import TerminalCapabilities


def create_backend(runtime, mode: str = "auto", **kwargs):
    mode = (mode or "auto").lower()
    if mode == "plain":
        return PlainLineUI(runtime, **kwargs)

    pt_available = True
    try:
        import prompt_toolkit  # noqa: F401
    except ImportError:
        pt_available = False

    if mode == "tui":
        if not pt_available:
            sys.stderr.write(
                "Error: prompt-toolkit is required for TUI mode.\n"
                "Install it using: pip install -e '.[tui]' (or pip install 'prompt-toolkit>=3.0.52,<4')\n"
            )
            sys.exit(2)
        from kitt.ui.backend import PromptToolkitBackend
        return PromptToolkitBackend(runtime, mode="tui", **kwargs)

    # mode == "auto"
    reason = None
    if os.environ.get("TERM") == "dumb":
        reason = "TERM=dumb"
    elif not TerminalCapabilities.is_tty() and kwargs.get("input") is None:
        reason = "stdin/stdout are not TTYs"
    elif not pt_available:
        reason = "prompt_toolkit is not installed"

    if reason:
        return PlainLineUI(runtime, reason=reason, **kwargs)

    from kitt.ui.backend import PromptToolkitBackend
    return PromptToolkitBackend(runtime, mode="auto", **kwargs)

