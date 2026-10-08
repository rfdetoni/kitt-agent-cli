from __future__ import annotations

from kitt.tools import registry_core as _core

# The operation catalog is immutable and authoritative in core_runtime.
# SafeRuntime implements specialized handlers without mutating or extending it.
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
        from kitt.core.completion_guard import install_completion_guard

        install_agent_engineering(processor, self)
        install_completion_guard(processor, self)
        return result

    def get_tool_definitions(self, enabled_tools=None):
        tools = super().get_tool_definitions(enabled_tools)
        for tool in tools:
            if tool.get("name") == "child_spawn":
                args = dict(tool.get("args") or {})
                args["backend"] = (
                    "optional explicit external backend: "
                    "codex|claude|opencode|aider|gemini|openhands|prime"
                )
                tool["args"] = args
            elif tool.get("name") == "kitt_runtime":
                # Keep the model-visible composite tool intentionally small.
                # Native adapters derive the exact operation enum structurally from
                # OPERATION_SPECS; do not duplicate that authority in prompt text.
                # Retain only the mutation guidance
                # that prevents common misrouting (artifact storage vs workspace
                # writes, or unified diff vs SEARCH/REPLACE patches).
                tool["description"] = (
                    "Safe runtime. process.run {argv:[...],cwd?,timeout_seconds?,network?:bool=false}; "
                    "argv-only/no shell; file edits use repo.write_file or patch.apply; "
                    "patch.apply accepts SEARCH/REPLACE or unified diff."
                )
                tool["args"] = {
                    "operation": "runtime operation",
                    "arguments": {
                        "type": "object",
                        "description": "Operation args; process.run never accepts command/cmd/args.",
                    },
                }
        return tools

    def execute_tool(self, tool_name, args=None, *positional, **kwargs):
        """Run one canonical policy-governed tool execution path."""
        if args is not None and not isinstance(args, dict):
            return ToolResult(False, "", f"Invalid arguments for {tool_name}: expected an object.")
        normalized_args = args or {}
        if tool_name == "run_command":
            argv = normalized_args.get("argv")
            if self.policy.evaluate_argv(argv) == "DENY":
                # Allow All removes routine approval prompts, not process
                # sandbox/path-scope or shell-execution restrictions.
                reason = "Use a direct executable and argv; shell wrappers and unsafe paths are forbidden."
                if not isinstance(argv, (list, tuple)) or not argv:
                    reason = "Expected argv as a non-empty list of executable and argument strings."
                return ToolResult(
                    False,
                    "",
                    f"Execution denied by PolicyEngine for tool 'run_command'. {reason}",
                )

        processor = getattr(self, "_processor", None)
        if (
            processor is not None
            and getattr(processor, "_agent_engineering_installed", False)
        ):
            from kitt.core.agent_runtime import execute_tool_with_engineering

            return execute_tool_with_engineering(
                processor,
                self,
                super().execute_tool,
                tool_name,
                normalized_args,
                *positional,
                **kwargs,
            )
        return super().execute_tool(
            tool_name,
            normalized_args,
            *positional,
            **kwargs,
        )


def __getattr__(name: str):
    try:
        return getattr(_core, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc


__all__ = ["ToolRegistry", "ToolResult", "runtime_operation_names", "compact_runtime_operation_catalog"]
