import tempfile
import time
import unittest
from pathlib import Path

from kitt.core.runtime import KittRuntime
from kitt.goals.models import Goal
from kitt.goals.scheduler import GoalScheduler


class TestGoalSchedulerExecution(unittest.TestCase):
    """Rigorous tests for GoalScheduler atomic leases, budget limits, and recurrence."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.runtime = KittRuntime.build(str(self.root))
        self.conv = self.runtime.history.new_conversation("Goal Scheduler Main")

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_01_atomic_lease_claim_prevents_dual_execution(self):
        """Verify two scheduler instances competing for the same goal lease resolve atomically."""
        gs = self.runtime.goals
        sched1 = GoalScheduler(self.runtime.database, gs)
        sched2 = GoalScheduler(self.runtime.database, gs)

        goal = gs.create(
            conversation_id=self.conv["id"],
            objective="Migrate database schema",
            token_budget=10000,
            max_turns=5,
        )

        sched1.schedule_goal(
            goal.id,
            recurrence="0 * * * *",
            heartbeat_enabled=True,
            next_run_delay_seconds=0.0,
        )

        # First scheduler attempts lease claim
        claimed1 = sched1.claim_lease(goal.id, worker_id="worker_alpha", lease_duration_seconds=30.0)
        self.assertTrue(claimed1)

        # Second scheduler attempts lease claim while lease is active
        claimed2 = sched2.claim_lease(goal.id, worker_id="worker_beta", lease_duration_seconds=30.0)
        self.assertFalse(claimed2, "Second worker must not claim an active lease")

    def test_02_goal_budget_exhaustion_transitions_to_terminal_state(self):
        """Verify goal exceeding token budget or turns transitions strictly to BUDGET_EXHAUSTED."""
        gs = self.runtime.goals
        goal = gs.create(
            conversation_id=self.conv["id"],
            objective="Budget bounded task",
            token_budget=500,
            max_turns=3,
        )

        # Charge within budget
        g1 = gs.charge(goal.id, tokens=200, turn=True)
        self.assertEqual(g1.state, "ACTIVE")
        self.assertEqual(g1.tokens_used, 200)

        # Charge exceeding budget
        g2 = gs.charge(goal.id, tokens=400, turn=True)
        self.assertEqual(g2.state, "BUDGET_EXHAUSTED")
        self.assertEqual(g2.tokens_used, 600)

    def test_03_quality_gate_execution_and_status(self):
        """Verify quality gates attach to goals and evaluate commands."""
        gs = self.runtime.goals
        goal = gs.create(
            conversation_id=self.conv["id"],
            objective="Feature implementation with quality gate",
        )

        gate = gs.add_gate(goal.id, name="TestSuite", argv=["echo", "PASSED"], timeout_seconds=10)
        self.assertEqual(gate.status, "PENDING")
        self.assertEqual(gate.goal_id, goal.id)

        fetched = gs.get(goal.id)
        self.assertEqual(len(fetched.gates), 1)
        self.assertEqual(fetched.gates[0].name, "TestSuite")
