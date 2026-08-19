import tempfile
import unittest
from pathlib import Path

from kitt.core.runtime_config import RuntimeConfig
from kitt.domain.entities import ContextPlan
from kitt.history.database import HistoryDatabase
from kitt.security.capabilities import CAP_PROCESS_RUN
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.handlers import ToolContext
from kitt.tools.handlers.safe_runtime import SafeRuntimeHandler
from kitt.tools.registry import ToolRegistry
from kitt.tools.surface_selector import ToolSurfaceSelector


class TestSafeRuntimeIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.db = HistoryDatabase(str(self.root))
        self.registry = ToolRegistry(root_dir=str(self.root))

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_tool_surface_selector_modes(self):
        plan = ContextPlan(enabled_tools=["read_file", "search", "write_file", "apply_patch", "run_command"])
        self.assertEqual(
            ToolSurfaceSelector(RuntimeConfig(tool_runtime_mode="safe_runtime")).select_tools(plan),
            ["kitt_runtime"],
        )
        self.assertEqual(
            ToolSurfaceSelector(RuntimeConfig(tool_runtime_mode="legacy")).select_tools(plan),
            plan.enabled_tools,
        )
        self.assertEqual(
            ToolSurfaceSelector(RuntimeConfig(tool_runtime_mode="auto", safe_runtime_enabled=True)).select_tools(plan),
            ["kitt_runtime"],
        )

    def test_handler_fails_closed_without_security_context(self):
        ctx = ToolContext(
            workspace_id="test_ws", conversation_id="test_conv", turn_id="turn_1",
            registry=self.registry, origin="MODEL",
        )
        res = SafeRuntimeHandler().execute(
            {"operation": "process.run", "arguments": {"command": "echo test"}}, ctx
        )
        self.assertFalse(res.success)
        self.assertFalse(res.requires_approval)
        self.assertIn("ExecutionSecurityContext", res.error)

    def test_handler_preserves_structured_approval_with_explicit_capability(self):
        sec = ExecutionSecurityContext.create_user_context(
            "test_ws", "test_conv", "turn_2", capabilities={CAP_PROCESS_RUN}
        )
        ctx = ToolContext(
            workspace_id="test_ws", conversation_id="test_conv", turn_id="turn_2",
            registry=self.registry, origin="MODEL", security_context=sec,
        )
        res = SafeRuntimeHandler().execute(
            {"operation": "process.run", "arguments": {"command": "echo test"}}, ctx
        )
        self.assertFalse(res.success)
        self.assertTrue(res.requires_approval)
        self.assertEqual(res.metadata["approval_action"], "run_command")
        self.assertEqual(res.metadata["approval_payload"], {"command": "echo test"})
