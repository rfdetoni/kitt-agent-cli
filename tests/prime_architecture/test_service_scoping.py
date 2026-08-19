import tempfile
import time
import unittest
from pathlib import Path

from kitt.artifacts.store import ArtifactStore
from kitt.core.runtime import KittRuntime
from kitt.goals.service import GoalService
from kitt.harness.repository import HarnessRepository
from kitt.harness.service import HarnessService
from kitt.history.database import HistoryDatabase
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.context import ExecutionSecurityContext


class TestServiceScoping(unittest.TestCase):
    """Rigorous multi-tenant workspace isolation tests across all core services."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        self.ws1_dir = self.root / "ws1"
        self.ws2_dir = self.root / "ws2"
        self.ws1_dir.mkdir(parents=True, exist_ok=True)
        self.ws2_dir.mkdir(parents=True, exist_ok=True)

        (self.ws1_dir / "secret_ws1.py").write_text("# Secret 1", encoding="utf-8")
        (self.ws2_dir / "secret_ws2.py").write_text("# Secret 2", encoding="utf-8")

        self.rt1 = KittRuntime.build(str(self.ws1_dir))
        self.rt2 = KittRuntime.build(str(self.ws2_dir))

    def tearDown(self):
        self.rt1.close()
        self.rt2.close()
        self.temp_dir.cleanup()

    def test_01_history_and_session_tree_workspace_isolation(self):
        """Verify conversations in workspace 1 are never returned when querying workspace 2."""
        c1 = self.rt1.history.new_conversation("WS1 Task")
        c2 = self.rt2.history.new_conversation("WS2 Task")

        ws1_convs = self.rt1.history.list_history(limit=50)
        ws2_convs = self.rt2.history.list_history(limit=50)

        ws1_ids = [c["id"] for c in ws1_convs]
        ws2_ids = [c["id"] for c in ws2_convs]

        self.assertIn(c1["id"], ws1_ids)
        self.assertNotIn(c2["id"], ws1_ids)

        self.assertIn(c2["id"], ws2_ids)
        self.assertNotIn(c1["id"], ws2_ids)

    def test_02_artifact_store_workspace_ownership_isolation(self):
        """Verify artifacts stored in workspace 1 cannot be listed or retrieved by workspace 2."""
        c1 = self.rt1.history.new_conversation("WS1 Artifact Task")
        art1 = self.rt1.artifacts.put(
            workspace_id=self.rt1.workspace_id,
            conversation_id=c1["id"],
            turn_id="turn_1",
            artifact_type="DIFF",
            content="diff content 1",
            summary="diff 1",
        )

        # Listing for workspace 2 must not show art1
        ws2_arts = self.rt2.artifacts.list(workspace_id=self.rt2.workspace_id)
        self.assertNotIn(art1.id, [a.id for a in ws2_arts])

        # Cross-workspace creation attempt must fail integrity checks
        with self.assertRaises(Exception):
            self.rt2.artifacts.put(
                workspace_id="invalid_ws_id",
                conversation_id=c1["id"],
                turn_id="turn_1",
                artifact_type="DIFF",
                content="invalid",
                summary="invalid",
            )

    def test_03_harness_service_workspace_scoping(self):
        """Verify learned harness patterns are strictly partitioned by workspace_id."""
        self.rt1.harness.remember(
            name="pattern_1",
            content="Workspace 1 coding style",
            workspace_id=self.rt1.workspace_id,
        )

        self.rt2.harness.remember(
            name="pattern_2",
            content="Workspace 2 coding style",
            workspace_id=self.rt2.workspace_id,
        )

        p1 = self.rt1.harness.prompt(workspace_id=self.rt1.workspace_id)
        p2 = self.rt2.harness.prompt(workspace_id=self.rt2.workspace_id)

        self.assertIn("Workspace 1 coding style", p1)
        self.assertNotIn("Workspace 2 coding style", p1)

        self.assertIn("Workspace 2 coding style", p2)
        self.assertNotIn("Workspace 1 coding style", p2)

    def test_04_safe_runtime_path_containment_and_workspace_boundary(self):
        """Verify SafeRuntime enforces containment strictly inside its own workspace root."""
        conv1 = self.rt1.history.new_conversation("WS1 Safe Runtime")
        safe_rt1 = SafeRuntime(
            workspace_root=self.ws1_dir,
            workspace_id=self.rt1.workspace_id,
            conversation_id=conv1["id"],
            tool_registry=self.rt1.registry,
            repository_index=self.rt1.repository_index,
            artifact_store=self.rt1.artifacts,
            child_manager=self.rt1.children,
            goal_service=self.rt1.goals,
            db=self.rt1.database,
        )

        sec_ctx = ExecutionSecurityContext.create_user_context(
            workspace_id=self.rt1.workspace_id,
            conversation_id=conv1["id"],
            capabilities={"repo.read"},
        )

        # Reading file inside WS1 succeeds
        res_ok = safe_rt1.execute("repo.read", {"path": "secret_ws1.py"}, security_context=sec_ctx)
        self.assertTrue(res_ok.success)

        # Attempting to read outside workspace (into WS2) must be blocked
        res_blocked = safe_rt1.execute("repo.read", {"path": "../ws2/secret_ws2.py"}, security_context=sec_ctx)
        self.assertFalse(res_blocked.success)
