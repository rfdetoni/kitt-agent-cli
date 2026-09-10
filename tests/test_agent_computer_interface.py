from __future__ import annotations

from kitt.runtime.core_runtime import OPERATION_SPECS
from kitt.runtime.programmatic_flow import READ_ONLY_FLOW_OPERATIONS
from kitt.tools.registry import (
    ToolRegistry,
    compact_runtime_operation_catalog,
    runtime_operation_names,
)


def _registry_without_runtime_dependencies() -> ToolRegistry:
    registry = object.__new__(ToolRegistry)
    registry._custom_tools = {}
    return registry


def _expand_compact_catalog(catalog: str) -> set[str]:
    operations: set[str] = set()
    for item in catalog.split(";"):
        if ".{" not in item:
            operations.add(item)
            continue
        namespace, raw = item.split(".{", 1)
        for operation in raw.removesuffix("}").split(","):
            operations.add(f"{namespace}.{operation}")
    return operations


def test_runtime_operation_catalog_is_generated_from_live_contract():
    expected = tuple(sorted(OPERATION_SPECS))

    assert runtime_operation_names() == expected
    assert ToolRegistry.runtime_operation_names() == expected
    assert _expand_compact_catalog(compact_runtime_operation_catalog()) == set(expected)

    registry = _registry_without_runtime_dependencies()
    definitions = registry.get_tool_definitions(["kitt_runtime"])

    assert len(definitions) == 1
    runtime_tool = definitions[0]
    assert runtime_tool["name"] == "kitt_runtime"
    assert runtime_tool["args"]["operation"] == compact_runtime_operation_catalog()
    assert _expand_compact_catalog(runtime_tool["args"]["operation"]) == set(expected)
    assert "live runtime contract" in runtime_tool["description"].lower()


def test_runtime_catalog_exposes_semantic_and_compressed_execution_operations():
    operations = set(runtime_operation_names())

    assert {
        "flow.execute",
        "repo.context_map",
        "repo.definition",
        "repo.hover",
        "repo.references_semantic",
        "repo.diagnostics",
        "repo.call_hierarchy",
        "repo.outline",
        "repo.ast_search",
        "security.scan",
        "session.search",
        "skill.call",
        "mcp.call",
    } <= operations


def test_programmatic_flow_only_composes_known_read_only_operations():
    assert READ_ONLY_FLOW_OPERATIONS <= set(OPERATION_SPECS)
    assert {
        "repo.definition",
        "repo.hover",
        "repo.references_semantic",
        "repo.diagnostics",
        "repo.call_hierarchy",
        "repo.outline",
        "repo.ast_search",
        "session.search",
    } <= READ_ONLY_FLOW_OPERATIONS

    assert {
        "flow.execute",
        "repo.edit_symbol",
        "patch.apply",
        "process.run",
        "children.spawn",
        "skill.call",
        "mcp.call",
        "security.scan",
        "state.set",
    }.isdisjoint(READ_ONLY_FLOW_OPERATIONS)
