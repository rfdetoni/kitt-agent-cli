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
    """Encode the live operation catalog compactly without losing exact names."""
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
        parts.append(
            f"{namespace}.{operations[0]}" if len(operations) == 1
            else f"{namespace}.{{{','.join(operations)}}}"
        )
    parts.extend(sorted(literals))
    return ";".join(parts)


class ToolRegistry(_core.ToolRegistry):
    """Stable registry facade and model-facing Agent Computer Interface."""

    @staticmethod
    def runtime_operation_names() -> tuple[str, ...]:
        return runtime_operation_names()

    def attach_processor(self, processor):
        result = super().attach_processor(processor)
        from kitt.core.agent_runtime import install_agent_engineering
        install_agent_engineering(processor, self)
        return result

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
                    "KITT live runtime contract. For workspace files use repo.write_file "
                    "with {path, content}; for directories use repo.create_directory; for "
                    "SEARCH/REPLACE edits use patch.apply. artifacts.store only persists "
                    "internal KITT artifacts outside model context and never creates or "
                    "updates workspace files."
                )
                args = dict(tool.get("args") or {})
                args["operation"] = {
                    "type": "string",
                    "description": f"Exact runtime operation. Available: {operation_hint}",
                }
                args["arguments"] = {
                    "type": "object",
                    "additionalProperties": True,
                    "description": (
                        "Operation-specific arguments. repo.write_file={path,content," 
                        "expected_content_hash?}; repo.create_directory={path}; "
                        "patch.apply={patch}; artifacts.store={content,artifact_type?,summary?}. "
                        "Never use artifacts.store to create or update a workspace file."
                    ),
                }
                tool["args"] = args
        return tools


def __getattr__(name: str):
    try:
        return getattr(_core, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc


__all__ = ["ToolRegistry", "ToolResult", "runtime_operation_names", "compact_runtime_operation_catalog"]
