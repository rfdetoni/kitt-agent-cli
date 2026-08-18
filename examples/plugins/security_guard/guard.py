"""Security guard plugin entrypoint."""
from __future__ import annotations


def setup(ctx):
    def before_tool(tool_call, hook_ctx):
        if isinstance(tool_call, dict) and tool_call.get("name") == "blocked_synthetic_tool":
            raise PermissionError("Tool blocked by security-guard plugin.")
        return tool_call

    ctx.hooks.register("tool.before_execute", before_tool, priority=100, fail_closed=True)
    return None
