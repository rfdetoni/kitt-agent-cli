"""Real KAP/2 literal dispatch must preserve compute capabilities and isolation."""
import tempfile
from pathlib import Path

from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import CAP_SAFE_COMPUTE
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.registry import ToolRegistry
from kitt_protocol import decode_kap2_frame


def test_kap2_literal_dispatches_safe_python_and_returns_structured_data():
    envelope = (
        "K2 T kitt_runtime\n"
        "operation: sandbox.python\n"
        "arguments:\n"
        "  code<<PY\n"
        "values = [10, 20, 30, 40]\n"
        "_result = {'average': sum(values) / len(values), 'total': sum(values)}\n"
        "PY\n"
        "K2/END"
    )
    frame = decode_kap2_frame(envelope)
    assert frame["tool"] == "kitt_runtime"
    with tempfile.TemporaryDirectory() as directory:
        registry = ToolRegistry(directory)
        runtime = SafeRuntime(Path(directory), "ws", "conv", tool_registry=registry)
        try:
            result = runtime.execute(
                frame["tool_input"]["operation"],
                frame["tool_input"]["arguments"],
                effective_capabilities={CAP_SAFE_COMPUTE},
            )
            assert result.success, result.error
            assert result.data["result"] == {"average": 25.0, "total": 100}
            assert isinstance(result.data["steps"], int)
        finally:
            registry.close()


def test_sandbox_python_denies_missing_capability_and_host_escape():
    with tempfile.TemporaryDirectory() as directory:
        registry = ToolRegistry(directory)
        runtime = SafeRuntime(Path(directory), "ws", "conv", tool_registry=registry)
        marker = Path(directory) / "must-not-exist"
        try:
            denied = runtime.execute(
                "sandbox.python", {"code": "_result = 1"},
                effective_capabilities=set(),
            )
            assert not denied.success
            assert "compute.safe" in denied.error
            for code in (
                f"open({str(marker)!r}, 'w')",
                "import os\n_result = 1",
            ):
                unsafe = runtime.execute(
                    "sandbox.python", {"code": code},
                    effective_capabilities={CAP_SAFE_COMPUTE},
                )
                assert not unsafe.success
                assert not marker.exists()
        finally:
            registry.close()


def test_sandbox_python_validates_scope_and_rejects_unknown_arguments():
    with tempfile.TemporaryDirectory() as directory:
        registry = ToolRegistry(directory)
        runtime = SafeRuntime(Path(directory), "ws", "conv", tool_registry=registry)
        other_workspace = ExecutionSecurityContext.create_user_context(
            workspace_id="other", conversation_id="conv", turn_id="t",
            capabilities={CAP_SAFE_COMPUTE},
        )
        try:
            scope = runtime.execute(
                "sandbox.python", {"code": "_result = 42"},
                security_context=other_workspace,
            )
            assert not scope.success
            assert "Cross-workspace" in scope.error
            for invalid in (
                {"code": "_result = 1", "network": True},
                {"code": "_result = 1", "inputs": ["bad"]},
                {"code": "_result = 1", "result_var": "__class__"},
            ):
                rejected = runtime.execute(
                    "sandbox.python", invalid,
                    effective_capabilities={CAP_SAFE_COMPUTE},
                )
                assert not rejected.success
        finally:
            registry.close()


def test_runtime_tool_capability_map_includes_safe_compute():
    from kitt.security.capabilities import capabilities_for_tools
    from kitt.tools.registry import runtime_operation_names

    assert CAP_SAFE_COMPUTE in capabilities_for_tools(["kitt_runtime"])
    assert "sandbox.python" in runtime_operation_names()

    runtime = SafeRuntime(Path("."), "ws", "conv")
    capabilities = runtime.execute("kap.capabilities", {})
    assert capabilities.success
    assert "sandbox.python" in capabilities.data["features"]
