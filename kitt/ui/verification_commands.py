"""UI helpers for KITT's persistent full-verification mode."""
from __future__ import annotations

from dataclasses import replace

from kitt.settings.runtime_flags import (
    set_verification_full_enabled,
    verification_full_enabled,
)


def _sync_runtime(runtime, enabled: bool) -> None:
    config = getattr(runtime, "config", None)
    if config is not None and hasattr(config, "verify_full_enabled"):
        try:
            runtime.config = replace(config, verify_full_enabled=enabled)
        except (TypeError, ValueError):
            pass
    processor = getattr(runtime, "processor", None)
    processor_config = getattr(processor, "config", None)
    if processor_config is not None and hasattr(processor_config, "verify_full_enabled"):
        try:
            processor.config = replace(processor_config, verify_full_enabled=enabled)
        except (TypeError, ValueError):
            pass


def apply_verification_mode(runtime, argument: str = "") -> str:
    value = str(argument or "").strip().casefold()
    current = verification_full_enabled(
        bool(getattr(getattr(runtime, "config", None), "verify_full_enabled", False))
    )
    if value in {"", "toggle"}:
        enabled = not current
    elif value in {"on", "1", "true", "enable", "enabled"}:
        enabled = True
    elif value in {"off", "0", "false", "disable", "disabled"}:
        enabled = False
    elif value in {"status", "show"}:
        return (
            f"Full verification: {'ON' if current else 'OFF'}\n"
            "Use /verify-full [on|off|toggle|status]."
        )
    else:
        return "Usage: /verify-full [on|off|toggle|status]"

    set_verification_full_enabled(enabled)
    _sync_runtime(runtime, enabled)
    if enabled:
        return (
            "Full verification ENABLED. KITT will run bounded project "
            "compile/typecheck/lint/test gates after edits."
        )
    return (
        "Full verification DISABLED. KITT will keep fast structural "
        "validation and targeted checks."
    )


def handle_verification_mode_command(ui, argument: str = "") -> None:
    ui._show_result(apply_verification_mode(ui.runtime, argument))
