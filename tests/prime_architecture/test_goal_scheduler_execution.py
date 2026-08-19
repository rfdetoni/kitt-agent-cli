import tempfile
import unittest
from pathlib import Path

from kitt.core.runtime import KittRuntime
from kitt.goals.scheduler import GoalScheduler


class TestGoalSchedulerExecution(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.runtime = KittRuntime.build(str(self.root))
        self.conv = self.runtime.history.new_conversation("Goal Scheduler Main")

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_atomic_lease_claim_prevents_dual_execution(self):
        goal = self.runtime.goals.create(self.conv["id"], "Inspect repository", token_budget=10000)
        one = GoalScheduler(self.runtime.database, self.runtime.goals)
        two = GoalScheduler(self.runtime.database, self.runtime.goals)
        self.assertTrue(one.claim_lease(goal.id, "one", 30))
        self.assertFalse(two.claim_lease(goal.id, "two", 30))

    def test_due_goal_without_executor_never_claims_success(self):
        goal = self.runtime.goals.create(self.conv["id"], "Inspect repository", token_budget=10000)
        scheduler = GoalScheduler(self.runtime.database, self.runtime.goals)
        scheduler.schedule_goal(goal.id, heartbeat_enabled=True)
        result = scheduler.check_and_execute_due()
        self.assertEqual(result[0]["status"], "DUE_NO_EXECUTOR")
        self.assertNotEqual(self.runtime.goals.get(goal.id).state, "SUCCEEDED")

    def test_quality_gate_persists(self):
        goal = self.runtime.goals.create(self.conv["id"], "Validated task")
        gate = self.runtime.goals.add_gate(goal.id, "TestSuite", ["echo", "PASSED"], 10)
        self.assertEqual(gate.status, "PENDING")
        self.assertEqual(len(self.runtime.goals.get(goal.id).gates), 1)
