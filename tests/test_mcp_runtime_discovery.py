"""Registered MCP discovery is read-only, bounded and capability gated."""
from __future__ import annotations

from types import SimpleNamespace

from kitt.runtime.core_runtime import SafeRuntime, OPERATION_REGISTRY
from kitt.security.capabilities import CAP_MCP_CALL


def test_discover_requires_mcp_capability_and_never_executes_tools():
    assert OPERATION_REGISTRY["mcp.discover"].required_capability == CAP_MCP_CALL
    class Registry:
        _custom_tools = {
            "mcp.trusted.binary_inspect": {
                "description": "Inspect binary metadata", "scope_aware": False
            },
            "mcp.trusted.evidence_query": {
                "description": "Query evidence", "scope_aware": True
            },
            "mcp.other.test": {"description": "No access", "scope_aware": True},
            "write_file": {"description": "Not MCP", "scope_aware": True},
        }
        def execute_tool(self, *args, **kwargs):
            raise AssertionError("Discovery must not execute tools")

    runtime = object.__new__(SafeRuntime)
    runtime.registry = Registry()
    all_results = runtime._op_mcp_discover(
        {"query": "", "limit": 1, "server_id": "trusted"},
        SimpleNamespace(is_path_scoped=False),
    )
    assert all_results.success
    assert len(all_results.data["tools"]) == 1
    assert all_results.data["truncated"]
    assert all_results.data["tools"][0]["name"] == "mcp.trusted.binary_inspect"
    scoped = runtime._op_mcp_discover(
        {"query": "evidence", "limit": 5},
        SimpleNamespace(is_path_scoped=True),
    )
    assert [x["name"] for x in scoped.data["tools"]] == ["mcp.trusted.evidence_query"]
    assert not runtime._op_mcp_discover(
        {"limit": 999}, SimpleNamespace(is_path_scoped=False)
    ).success
