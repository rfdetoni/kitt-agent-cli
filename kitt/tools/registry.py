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


def compact_runtime_operation_catalog() -> str:
    """Encode the live operation catalog compactly without losing exact names.

    Names sharing a namespace are rendered as ``repo.{read,search}`` instead of
    repeating the prefix for every operation. This keeps the ACI complete while
    preserving the SafeRuntime token-reduction contract.
    """

    grouped: dict[str, list[str]] = {}
    literals: list[str] = []
    for name in runtime_operation_names():
        namespace, separator, operation = name.partition(".")
        if separator:
            grouped.setdefault(namespace, []).append(operation)
        else:
            literals.append(name)

    parts: list[str] = []
    for namespace in sorted(grouped):
        operations = sorted(grouped[namespace])
        if len(operations) == 1:
            parts.append(f"{namespace}.{operations[0]}")
        else:
            parts.append(f"{namespace}.{{{','.join(operations)}}}")
    parts.extend(sorted(literals))
    return ";".join(parts)


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
        operation_hint = compact_runtime_operation_catalog()
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
                    "Compact policy-governed KITT Agent Computer Interface; "
                    "args.operation is generated from the live runtime contract."
                )
                args = dict(tool.get("args") or {})
                args["operation"] = operation_hint
                args["arguments"] = "operation-specific JSON object; validated fail-closed"
                tool["args"] = args
        return tools


def __getattr__(name: str):
    try:
        return getattr(_core, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc


__all__ = [
    "ToolRegistry",
    "ToolResult",
    "runtime_operation_names",
    "compact_runtime_operation_catalog",
]
