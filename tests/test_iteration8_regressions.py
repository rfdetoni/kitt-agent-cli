import os
import shutil
import tempfile
import unittest
from pathlib import Path

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.goals.models import Goal, QualityGate
from kitt.goals.service import GoalService
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, canonical_workspace_path
from kitt.history.service import HistoryService
from kitt.history.session_tree import SessionTreeRepository
from kitt.metrics.collector import MetricsCollector
from kitt.metrics.models import TurnMetrics
from kitt.tools.policy_engine import PolicyEngine

class TestIteration8Regressions(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.root_path = Path(self.temp_dir)
        self.db = HistoryDatabase(self.temp_dir, in_memory=True)
        self.tree = SessionTreeRepository(self.db)
        self.history_repo = HistoryRepository(self.db)
        self.ws = self.history_repo.get_or_create_workspace(self.temp_dir)
        self.conv = self.history_repo.create_conversation(self.ws["id"], "Test Conv")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_goal_add_gate_signature_compat(self):
        service = GoalService(self.db)
        goal = service.create(self.conv["id"], "Test Objective")
        gate = service.add_gate(goal_id=goal.id, name="TestGate", argv=["pytest"], timeout_seconds=60)
        self.assertIsInstance(gate, QualityGate)
        self.assertEqual(gate.name, "TestGate")
        self.assertEqual(gate.argv, ["pytest"])

    def test_02_child_spawn_signature_compat(self):
        with KittRuntime.build(self.temp_dir) as rt:
            active_conv = rt.history.get_or_create_active()
            child = rt.children.spawn(
                parent_conversation_id=active_conv["id"],
                parent_turn_id="turn_1",
                name="my_child",
                task="Inspect repo",
                workspace_id=rt.history.workspace["id"],
                allowed_tools=["read_file"]
            )
            self.assertEqual(child.name, "my_child")
            self.assertEqual(child.task, "Inspect repo")

    def test_03_continue_turn_none_grant_fails_gracefully(self):
        with KittRuntime.build(self.temp_dir) as rt:
            events = list(rt.processor.continue_turn("non_existent_turn", None))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].__class__.__name__, "TurnFailed")
            self.assertIn("No valid approval grant provided", events[0].error)

    def test_04_history_service_injects_session_tree(self):
        service = HistoryService(self.temp_dir, db=self.db, tree=self.tree)
        self.assertTrue(hasattr(service, "tree"))
        self.assertIs(service.tree, self.tree)

    def test_05_no_history_creates_no_disk_files(self):
        empty_dir = tempfile.mkdtemp()
        try:
            cfg = RuntimeConfig(history_enabled=False)
            with KittRuntime.build(empty_dir, config=cfg) as rt:
                snap = rt.snapshot()
                self.assertIsNotNone(snap)
            history_sqlite = Path(empty_dir) / ".kitt" / "history" / "history.sqlite3"
            self.assertFalse(history_sqlite.exists())
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

    def test_06_canonical_workspace_path_identity(self):
        hash1 = canonical_workspace_path(self.temp_dir)
        hash2 = canonical_workspace_path(os.path.join(self.temp_dir, ".", "..", Path(self.temp_dir).name))
        self.assertEqual(hash1, hash2)

    def test_07_goal_service_finish_string_interpretation(self):
        service = GoalService(self.db)
        goal = service.create(self.conv["id"], "Objective")
        finished_failed = service.finish(goal.id, "FAILED")
        self.assertEqual(finished_failed.state, "FAILED")

    def test_08_metrics_collector_record_handles_dict(self):
        collector = MetricsCollector()
        collector.record({"turn_id": "t1", "input_tokens": 100, "output_tokens": 50, "duration_ms": 12.5})
        summary = collector.get_summary()
        self.assertEqual(summary["total_turns"], 1)

    def test_09_policy_engine_ask_on_model_autonomy_tools(self):
        policy = PolicyEngine(self.temp_dir)
        self.assertEqual(policy.evaluate_tool("child_spawn", origin="MODEL"), "ALLOW")
        self.assertEqual(policy.evaluate_tool("harness_remember", origin="MODEL"), "ASK")
        self.assertEqual(policy.evaluate_tool("goal_create", origin="MODEL"), "ASK")
        self.assertEqual(policy.evaluate_tool("read_file", origin="MODEL"), "ALLOW")

    def test_10_goal_gates_field_populated(self):
        service = GoalService(self.db)
        goal = service.create(self.conv["id"], "Objective")
        service.add_gate(goal.id, "Check1", ["python3", "-m", "unittest"])
        updated_goal = service.get(goal.id)
        self.assertTrue(hasattr(updated_goal, "gates"))
        self.assertEqual(len(updated_goal.gates), 1)
        self.assertEqual(updated_goal.gates[0].name, "Check1")

if __name__ == "__main__":
    unittest.main()
