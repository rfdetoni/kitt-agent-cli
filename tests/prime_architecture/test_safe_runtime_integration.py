import tempfile
import unittest
from pathlib import Path

from kitt.core.runtime_config import RuntimeConfig
from kitt.domain.entities import ContextPlan
from kitt.history.database import HistoryDatabase
from kitt.tools.handlers import ToolContext
from kitt.tools.handlers.safe_runtime import SafeRuntimeHandler
from kitt.tools.registry import ToolRegistry
from kitt.tools.surface_selector import ToolSurfaceSelector


class TestSafeRuntimeIntegration(unittest.TestCase):
    """Integration tests for SafeRuntimeHandler, ToolSurfaceSelector, and TurnProcessor modes."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.db = HistoryDatabase(str(self.root))
        self.registry = ToolRegistry(root_dir=str(self.root))
        (self.root / "file1.py").write_text("print('hello')", encoding="utf-8")

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_01_tool_surface_selector_modes(self):
        """Verify ToolSurfaceSelector accurately selects tools for legacy, safe_runtime, and auto modes."""
        plan = ContextPlan(
            enabled_tools=["read_file", "search", "write_file", "apply_patch", "run_command"],
        )

        # 1. safe_runtime mode -> exposes only kitt_runtime
        selector_safe = ToolSurfaceSelector(config=RuntimeConfig(tool_runtime_mode="safe_runtime"))
        tools_safe = selector_safe.select_tools(plan)
        self.assertEqual(tools_safe, ["kitt_runtime"])

        # 2. legacy mode -> exposes plan.enabled_tools
        selector_legacy = ToolSurfaceSelector(config=RuntimeConfig(tool_runtime_mode="legacy"))
        tools_legacy = selector_legacy.select_tools(plan)
        self.assertEqual(tools_legacy, plan.enabled_tools)

        # 3. auto mode with safe_runtime_enabled -> exposes kitt_runtime
        selector_auto = ToolSurfaceSelector(config=RuntimeConfig(tool_runtime_mode="auto", safe_runtime_enabled=True))
        tools_auto = selector_auto.select_tools(plan)
        self.assertEqual(tools_auto, ["kitt_runtime"])

        # 4. auto mode with safe_runtime_enabled=False -> falls back to legacy tools
        selector_fallback = ToolSurfaceSelector(config=RuntimeConfig(tool_runtime_mode="auto", safe_runtime_enabled=False))
        tools_fallback = selector_fallback.select_tools(plan)
        self.assertEqual(tools_fallback, plan.enabled_tools)

    def test_02_safe_runtime_handler_approval_flow(self):
        """Verify SafeRuntimeHandler converts approval requirement into ToolResult with requires_approval=True."""
        handler = SafeRuntimeHandler()
        ctx = ToolContext(
            workspace_id="test_ws",
            conversation_id="test_conv",
            turn_id="turn_101",
            registry=self.registry,
            origin="MODEL",
        )

        res = handler.execute(
            {"operation": "process.run", "arguments": {"command": "pytest"}},
            ctx,
        )

        self.assertFalse(res.success)
        self.assertTrue(res.requires_approval)
        self.assertEqual(res.metadata.get("approval_action"), "run_command")
        self.assertEqual(res.metadata.get("approval_payload"), {"command": "pytest"})
        self.assertEqual(res.metadata.get("required_capability"), "process.run")
