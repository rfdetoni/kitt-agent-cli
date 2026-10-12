"""REA integration through KITT's governed local MCP boundary."""
from __future__ import annotations

import json
import shutil

REA_SERVER_ID = "rea"


# Only suggestions: REA owns provider execution and canonical Evidence.
WORKFLOWS = {
    "native": {
        "tools": ["open_binary", "analyze_function", "get_call_graph"],
        "preflight": "rea doctor --provider ghidra --json",
        "limitations": "Static analysis does not prove runtime behavior. Select one deep provider.",
    },
    "javascript": {
        "tools": ["analyze_javascript_application", "trace_application_feature",
                  "inspect_analysis_view"],
        "preflight": "rea analyze-javascript-application /path/to/artifact --json",
        "limitations": "Preserve static unknowns; do not claim runtime execution.",
    },
    "electron": {
        "tools": ["analyze_javascript_application", "inspect_electron_page",
                  "reconcile_javascript_runtime", "trace_application_feature"],
        "preflight": "rea doctor --json",
        "limitations": "Runtime observation requires explicit scope and verified identity.",
    },
    "managed": {
        "tools": ["inspect_managed_artifact", "inspect_managed_members",
                  "inspect_managed_native_boundaries", "verify_managed_native_boundaries"],
        "preflight": "rea inspect-managed-artifact /path/to/assembly.dll --json",
        "limitations": "CIL metadata does not verify native implementations.",
    },
    "comparison": {
        "tools": ["compare_application_versions", "compare_managed_members",
                  "compare_functions"],
        "preflight": "rea doctor --json",
        "limitations": "Use artifact-compatible comparisons and authenticated Evidence.",
    },
    "crash": {
        "tools": ["inspect_recorded_crash"],
        "preflight": "rea doctor --json",
        "limitations": "Recorded crash metadata does not authorize live debugging.",
    },
    "network": {
        "tools": ["inspect_packet_capture"],
        "preflight": "rea doctor --json",
        "limitations": "Examine existing PCAPs only; capture acquisition requires separate authorization.",
    },
}


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

    def workflow(args=None):
        query = args or {}
        category = query.get("target_type") if isinstance(query, dict) else None
        if category not in WORKFLOWS:
            return json.dumps({
                "error": "unsupported_target_type",
                "valid_target_types": sorted(WORKFLOWS),
            }, sort_keys=True)
        selected = WORKFLOWS[category]
        return json.dumps({
            "target_type": category,
            "mcp_prefix": "mcp.rea.",
            "suggested_tools": selected["tools"],
            "preflight": selected["preflight"],
            "limitations": selected["limitations"],
            "execution": "guidance_only; invoke REA tools under KITT approval policy",
            "evidence": "preserve full REA Evidence including coverage, unknowns, provenance",
            "guest_access": "no implicit VM network or shell access",
        }, sort_keys=True)

    ctx.tools.register(
        "rea_workflow",
        workflow,
        description="Suggest evidence-backed REA workflows for native, JavaScript, Electron, .NET, comparison, crash and PCAP analysis; guidance only.",
        schema={
            "type": "object",
            "properties": {"target_type": {"type": "string", "enum": sorted(WORKFLOWS)}},
            "required": ["target_type"],
            "additionalProperties": False,
        },
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
