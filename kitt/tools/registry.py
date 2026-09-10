from __future__ import annotations

from kitt.tools import registry_core as _core


ToolResult = _core.ToolResult


class ToolRegistry(_core.ToolRegistry):
    """Stable registry facade that keeps model-facing contracts compact."""

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
                tool["description"] = (
                    "Execute policy-governed KITT runtime operations including "
                    "repository retrieval/editing, semantic LSP/AST queries, "
                    "changed-file security scans, artifacts, process, children, "
                    "goals, memory, state and handles."
                )
        return tools


def __getattr__(name: str):
    try:
        return getattr(_core, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc


__all__ = ["ToolRegistry", "ToolResult"]
