"""Tests for dynamic tool integration in ToolRegistry from plugins and MCP."""
import tempfile
import unittest
from pathlib import Path

from kitt.security.capabilities import CAP_REPO_WRITE
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.registry import ToolRegistry


class TestExtensionTools(unittest.TestCase):

    def test_dynamic_tool_registration_and_execution(self):
        reg = ToolRegistry()

        def my_custom_tool(args):
            name = args.get("name", "World")
            return f"Hello, {name}!"

        reg.register(
            "custom_greet",
            my_custom_tool,
            description="Greet someone",
            schema={"name": "string"},
            owner_plugin_id="plugin:greeter",
        )

        # 1. Verify tool appears in definitions
        defs = reg.get_tool_definitions()
        self.assertTrue(any(t["name"] == "custom_greet" for t in defs))

        # 2. Execute tool
        result = reg.execute_tool("custom_greet", {"name": "Alice"}, origin="SAFE_RUNTIME_BROKER")
        self.assertTrue(result.success)
        self.assertEqual(result.output, "Hello, Alice!")

        # 3. Unload by owner
        removed = reg.unregister_by_owner("plugin:greeter")
        self.assertEqual(removed, 1)

        # 4. Verify tool is no longer available
        res_after = reg.execute_tool("custom_greet", {"name": "Alice"}, origin="SAFE_RUNTIME_BROKER")
        self.assertFalse(res_after.success)


if __name__ == "__main__":
    unittest.main()



class _CoordinatorProbe:
    def __init__(self):
        self.calls = []

    def claim_paths(self, paths, owner_id, intent, *, wait_timeout=0.0):
        self.calls.append((list(paths), owner_id, intent, wait_timeout))
        return [
            type(
                "Grant",
                (),
                {"resource_id": f"path:{path}", "owner_id": owner_id},
            )()
            for path in paths
        ]


class TestExtensionToolContracts(unittest.TestCase):
    def test_dynamic_tool_cannot_replace_builtin(self):
        reg = ToolRegistry()
        try:
            with self.assertRaises(ValueError):
                reg.register(
                    "read_file",
                    lambda _args: "shadowed",
                    owner_plugin_id="plugin:bad",
                )
        finally:
            reg.close()

    def test_dynamic_tool_schema_must_be_bounded_json_object(self):
        reg = ToolRegistry()
        try:
            with self.assertRaises(TypeError):
                reg.register(
                    "bad_schema",
                    lambda _args: "nope",
                    schema=["not", "an", "object"],
                    owner_plugin_id="plugin:bad",
                )
            with self.assertRaises(ValueError):
                reg.register(
                    "bad name",
                    lambda _args: "nope",
                    owner_plugin_id="plugin:bad",
                )
        finally:
            reg.close()

    def test_child_workspace_write_is_fenced_before_handler_runs(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg = ToolRegistry(root_dir=tmp)
            probe = _CoordinatorProbe()
            reg.coordinator = probe
            reg.policy.evaluate_tool = lambda *_args, **_kwargs: "ALLOW"
            context = ExecutionSecurityContext(
                workspace_id="ws",
                conversation_id="conv",
                turn_id="turn",
                origin="AGENT",
                principal_type="CHILD",
                principal_id="child-1",
                capabilities=frozenset({CAP_REPO_WRITE}),
                trace_id="trace",
            )
            try:
                result = reg.execute_tool(
                    "write_file",
                    {"path": "src/app.py", "content": "value = 1\n"},
                    turn_id="turn",
                    conversation_id="conv",
                    workspace_id="ws",
                    enabled_tools=["write_file"],
                    origin="AGENT",
                    security_context=context,
                )
                self.assertTrue(result.success, result.error)
                self.assertEqual(
                    probe.calls,
                    [(["src/app.py"], "child-1", "write_file mutation", 8.0)],
                )
                self.assertEqual(
                    result.metadata["coordination"]["resources"],
                    ["path:src/app.py"],
                )
                self.assertEqual(
                    (Path(tmp) / "src" / "app.py").read_text(encoding="utf-8"),
                    "value = 1\n",
                )
            finally:
                reg.close()
