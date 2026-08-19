import tempfile
import unittest
from pathlib import Path

from kitt.history.database import HistoryDatabase
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import (
    CAP_REPO_READ,
    CAP_REPO_WRITE,
    CAP_PROCESS_RUN,
)
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.registry import ToolRegistry


class TestSafeRuntimeSecurity(unittest.TestCase):
    """Rigorous security regression tests for ExecutionSecurityContext, fail-closed behavior, and capability enforcement."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.db = HistoryDatabase(str(self.root))
        self.registry = ToolRegistry(root_dir=str(self.root))
        self.runtime = SafeRuntime(
            workspace_root=self.root,
            workspace_id="test_ws",
            conversation_id="test_conv",
            tool_registry=self.registry,
            db=self.db,
        )

        # Seed a test file in workspace
        (self.root / "example.txt").write_text("Hello Safe Runtime Security", encoding="utf-8")

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_01_runtime_missing_capabilities_denied_fail_closed(self):
        """Verify executing an operation without security context or capabilities fails closed."""
        # 1. No security context and no capabilities provided -> fail closed
        res = self.runtime.execute("repo.read", {"path": "example.txt"})
        self.assertFalse(res.success)
        self.assertIn("fail-closed", res.error)

        # 2. Empty capabilities -> fail closed
        ctx_empty = ExecutionSecurityContext(
            workspace_id="test_ws",
            conversation_id="test_conv",
            turn_id="t1",
            origin="USER",
            principal_type="USER",
            principal_id="u1",
            capabilities=frozenset(),
            trace_id="tr1",
        )
        res_empty = self.runtime.execute("repo.read", {"path": "example.txt"}, security_context=ctx_empty)
        self.assertFalse(res_empty.success)
        self.assertIn("fail-closed", res_empty.error)

    def test_02_runtime_capability_read_allowed(self):
        """Verify read operation succeeds when CAP_REPO_READ is explicitly granted."""
        ctx = ExecutionSecurityContext(
            workspace_id="test_ws",
            conversation_id="test_conv",
            turn_id="t1",
            origin="USER",
            principal_type="USER",
            principal_id="u1",
            capabilities=frozenset([CAP_REPO_READ]),
            trace_id="tr1",
        )
        res = self.runtime.execute("repo.read", {"path": "example.txt"}, security_context=ctx)
        self.assertTrue(res.success, msg=res.error)
        self.assertIn("Hello Safe Runtime Security", str(res.data))

    def test_03_runtime_capability_write_denied_without_cap(self):
        """Verify write operation is blocked when principal only has CAP_REPO_READ."""
        ctx = ExecutionSecurityContext(
            workspace_id="test_ws",
            conversation_id="test_conv",
            turn_id="t1",
            origin="USER",
            principal_type="USER",
            principal_id="u1",
            capabilities=frozenset([CAP_REPO_READ]),
            trace_id="tr1",
        )
        res = self.runtime.execute("patch.apply", {"path": "example.txt", "patch": "diff"}, security_context=ctx)
        self.assertFalse(res.success)
        self.assertIn("fail-closed", res.error)

    def test_04_child_cannot_escalate_parent_capabilities(self):
        """Verify child agent derives strictly intersected privileges and cannot escalate."""
        parent_ctx = ExecutionSecurityContext(
            workspace_id="test_ws",
            conversation_id="test_conv",
            turn_id="t1",
            origin="USER",
            principal_type="USER",
            principal_id="parent_user",
            capabilities=frozenset([CAP_REPO_READ]),  # Parent only has read
            trace_id="tr_parent",
        )

        # Child requests write and process.run
        child_ctx = parent_ctx.derive_child_context(
            child_id="child_worker_1",
            requested_capabilities=[CAP_REPO_READ, CAP_REPO_WRITE, CAP_PROCESS_RUN],
        )

        # Child effective capabilities must be limited to CAP_REPO_READ
        self.assertIn(CAP_REPO_READ, child_ctx.capabilities)
        self.assertNotIn(CAP_REPO_WRITE, child_ctx.capabilities)
        self.assertNotIn(CAP_PROCESS_RUN, child_ctx.capabilities)

        # Execution using child context for write must be denied
        res = self.runtime.execute("patch.apply", {"path": "example.txt"}, security_context=child_ctx)
        self.assertFalse(res.success)
        self.assertIn("fail-closed", res.error)

    def test_05_policy_engine_ask_suspends_with_requires_approval(self):
        """Verify SafeRuntime returns structured approval requirement when PolicyEngine returns ASK."""
        ctx = ExecutionSecurityContext(
            workspace_id="test_ws",
            conversation_id="test_conv",
            turn_id="t1",
            origin="MODEL",
            principal_type="USER",
            principal_id="u1",
            capabilities=frozenset([CAP_PROCESS_RUN]),
            trace_id="tr1",
        )

        res = self.runtime.execute("process.run", {"command": "echo test"}, security_context=ctx, origin="MODEL")
        self.assertFalse(res.success)
        self.assertTrue(res.requires_approval)
        self.assertEqual(res.approval_action, "run_command")
        self.assertEqual(res.approval_payload, {"command": "echo test"})
        self.assertEqual(res.required_capability, CAP_PROCESS_RUN)
