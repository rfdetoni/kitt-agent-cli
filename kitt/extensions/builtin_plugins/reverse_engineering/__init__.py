"""Opt-in read-only reverse engineering MCP plugin."""
from __future__ import annotations

from pathlib import Path
import sys


class _Handle:
    def __init__(self, ctx):
        self.ctx = ctx

    async def start(self) -> None:
        workspace = Path(self.ctx.workspace_root).resolve(strict=True)
        self.ctx.mcp.register_stdio(
            "reverse-engineering", sys.executable,
            args=["-m", "kitt.extensions.builtin_plugins.reverse_engineering.server", "--workspace", str(workspace)],
            enabled=True, trust="restricted", allow_tools=["binary_inspect", "evidence_compare", "evidence_probe_compare"],
            timeout_seconds=20.0, max_output_bytes=2 * 1024 * 1024,
        )

    async def stop(self) -> None:
        await self.ctx.mcp.unregister_owned()


def setup(ctx):
    return _Handle(ctx)
