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

def test_describe_exposes_only_bounded_registered_schema_without_execution():
    assert OPERATION_REGISTRY["mcp.describe"].required_capability == CAP_MCP_CALL

    class Registry:
        _custom_tools = {
            "mcp.good.read": {
                "description": "Read evidence",
                "scope_aware": True,
                "trusted_read_only": True,
                "args": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
            "mcp.restricted.write": {
                "description": "Change evidence",
                "scope_aware": False,
                "trusted_read_only": False,
                "args": {"type": "object"},
            },
            "mcp.huge.read": {
                "scope_aware": True,
                "args": {"type": "object", "description": "x" * 17000},
            },
            "not_mcp.read": {"scope_aware": True, "args": {"type": "object"}},
        }

        def execute_tool(self, *args, **kwargs):
            raise AssertionError("Describing a tool cannot execute it")

    runtime = object.__new__(SafeRuntime)
    runtime.registry = Registry()
    scope = SimpleNamespace(is_path_scoped=True)
    result = runtime._op_mcp_describe({"tool_name": "mcp.good.read"}, scope)
    assert result.success
    assert result.data["name"] == "mcp.good.read"
    assert result.data["input_schema"]["properties"]["query"]["type"] == "string"
    assert result.data["trusted_read_only"] is True
    assert result.data["descriptions_and_schema_untrusted"] is True

    for name in ("mcp.restricted.write", "mcp.missing", "not_mcp.read", "mcp.huge.read"):
        assert not runtime._op_mcp_describe({"tool_name": name}, scope).success
    assert not runtime._op_mcp_describe({"tool_name": "mcp.good.read", "other": True}, scope).success

    unrestricted = SimpleNamespace(is_path_scoped=False)
    assert runtime._op_mcp_describe({"tool_name": "mcp.restricted.write"}, unrestricted).success


def test_program_mode_allows_mcp_schema_discovery_but_not_execution():
    from kitt.runtime.programmatic_flow import READ_ONLY_FLOW_OPERATIONS

    assert "mcp.discover" in READ_ONLY_FLOW_OPERATIONS
    assert "mcp.describe" in READ_ONLY_FLOW_OPERATIONS
    assert "mcp.call" not in READ_ONLY_FLOW_OPERATIONS
