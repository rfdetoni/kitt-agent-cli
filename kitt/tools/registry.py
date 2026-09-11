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
        from kitt.core.attachment_runtime import install_attachment_runtime
        from kitt.core.completion_guard import install_completion_guard

        install_agent_engineering(processor, self)
        install_attachment_runtime(processor)
        install_completion_guard(processor, self)
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
                    "KITT runtime. New/full file: repo.write_file {path,content}; "
                    "directory: repo.create_directory {path}; patch.apply edits existing files "
                    "with SEARCH/REPLACE only, never unified diff. Execute writes; no shell/manual-save "
                    "substitutes. artifacts.store is internal."
                )
                args = dict(tool.get("args") or {})
                args["operation"] = operation_hint
                args["arguments"] = {
                    "type": "object",
                    "additionalProperties": True,
                    "description": (
                        "New/full file=repo.write_file {path,content}; directory=repo.create_directory "
                        "{path}; existing edit=patch.apply {patch} SEARCH/REPLACE only, not unified diff."
                    ),
                }
                tool["args"] = args
        return tools

    def execute_tool(self, tool_name, args=None, *positional, **kwargs):
        """Enforce command DENY decisions before approval can be requested."""
        normalized_args = args or {}
        if tool_name == "run_command":
            command = str(normalized_args.get("command", "")).strip()
            if self.policy.evaluate_command(command) == "DENY":
                return ToolResult(
                    False,
                    "",
                    "Execution denied by PolicyEngine for tool 'run_command'.",
                )
        return super().execute_tool(tool_name, normalized_args, *positional, **kwargs)


def __getattr__(name: str):
    try:
        return getattr(_core, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc


__all__ = ["ToolRegistry", "ToolResult", "runtime_operation_names", "compact_runtime_operation_catalog"]
