from __future__ import annotations

from kitt.tools import registry_core as _core

# ``registry_core`` imports SafeRuntimeHandler, which loads the semantic runtime
# extensions that augment OPERATION_SPECS. Importing the catalog afterwards
# keeps the model-facing interface synchronized with the live runtime.
from kitt.runtime.core_runtime import OPERATION_SPECS


ToolResult = _core.ToolResult


def runtime_operation_names() -> tuple[str, ...]:
    """Return the live, authoritative KITT runtime operation catalog."""

    return tuple(sorted(str(name) for name in OPERATION_SPECS))


class ToolRegistry(_core.ToolRegistry):
    """Stable registry facade and model-facing Agent Computer Interface.

    Runtime operation discovery is generated from the executable contract rather
    than a second hand-maintained list. This keeps provider schemas and prompts
    from silently drifting behind KITT's actual capabilities.
    """

    @staticmethod
    def runtime_operation_names() -> tuple[str, ...]:
        return runtime_operation_names()

    def get_tool_definitions(self, enabled_tools=None):
        tools = super().get_tool_definitions(enabled_tools)
        operation_hint = "one of: " + ", ".join(self.runtime_operation_names())
        for tool in tools:
            if tool.get("name") == "child_spawn":
                args = dict(tool.get("args") or {})
                args["backend"] = (
                    "optional explicit external backend: "
                    "codex|claude|opencode|aider|gemini|openhands|prime"
                )
                tool["args"] = args
            elif tool.get("name") == "kitt_runtime":
                tool["description"] = (
                    "Authoritative, policy-governed KITT Agent Computer Interface. "
                    "The operation catalog in args.operation is generated from the "
                    "live runtime contract and is complete for this build."
                )
                args = dict(tool.get("args") or {})
                args["operation"] = operation_hint
                args["arguments"] = (
                    "JSON object for the selected operation; operation-specific "
                    "contracts are validated fail-closed by the runtime"
                )
                tool["args"] = args
        return tools


def __getattr__(name: str):
    try:
        return getattr(_core, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc


__all__ = ["ToolRegistry", "ToolResult", "runtime_operation_names"]
