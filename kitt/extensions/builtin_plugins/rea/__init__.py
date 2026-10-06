"""REA integration through KITT's governed local MCP boundary."""
from __future__ import annotations

import json
import shutil

REA_SERVER_ID = "rea"


class _ReaPluginHandle:
    def __init__(self, ctx):
        self.ctx = ctx

    async def start(self) -> None:
        executable = shutil.which("rea")
        if not executable:
            self.ctx.logger.info(
                "REA CLI is not installed; leaving the optional REA MCP adapter inactive."
            )
            return
        registration = self.ctx.mcp.register_stdio(
            REA_SERVER_ID,
            executable,
            args=["mcp"],
            enabled=True,
            trust="restricted",
            timeout_seconds=60.0,
            max_output_bytes=4 * 1024 * 1024,
        )
        if registration.get("registered"):
            self.ctx.logger.info("Registered local REA MCP server via %s mcp", executable)

    async def stop(self) -> None:
        await self.ctx.mcp.unregister_owned()


def setup(ctx):
    def inspect(args=None):
        executable = shutil.which("rea")
        servers = [
            server
            for server in ctx.mcp.list_servers()
            if str(server.get("server_id") or "").lower() == REA_SERVER_ID
        ]
        return json.dumps(
            {
                "available": bool(executable),
                "executable": executable,
                "mcp_servers": servers,
                "mcp_tool_prefix": "mcp.rea." if servers else None,
                "execution": (
                    "Reverse-engineering operations execute only through KITT's MCP, "
                    "tool-policy, approval, audit and cancellation boundaries."
                ),
                "installation": (
                    "KITT never downloads or updates REA implicitly. Install rea-agents "
                    "separately, then enable kitt-rea."
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    ctx.tools.register(
        "rea_inspect",
        inspect,
        description=(
            "Report local REA availability and the governed MCP namespace used for "
            "reverse-engineering operations."
        ),
        schema={},
    )
    return _ReaPluginHandle(ctx)
