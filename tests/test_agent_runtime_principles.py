from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.core.agent_runtime import (
    DurableTurnJournal,
    _turn,
    adaptive_retrieval_ratio,
    routing_feedback_snapshot,
)
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.session_state import SessionState
from kitt.core.turn_command import TurnCommand
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity
from kitt.metrics.admission import AgentAdmissionGate, AgentScorecard
from kitt.runtime.programmatic_flow import _transform
from kitt.validation.orchestrator import VerificationOrchestrator


class AgentRuntimePrinciplesTests(unittest.TestCase):
    def test_adaptive_retrieval_expands_ambiguous_high_risk_work(self):
        processor = SimpleNamespace(config=RuntimeConfig())
        exact = SimpleNamespace(risk="LOW", intent="IMPLEMENT", confidence=0.95, paths=("a.py",), symbols=("A",))
        ambiguous = SimpleNamespace(risk="HIGH", intent="DEBUG", confidence=0.4, paths=(), symbols=())
        cmd = TurnCommand(conversation_id="c", prompt="x")
        self.assertLess(adaptive_retrieval_ratio(processor, exact, cmd),
                        adaptive_retrieval_ratio(processor, ambiguous, cmd))

    def test_routing_feedback_snapshot_uses_one_query_for_all_profiles(self):
        class FakeConnection:
            def __init__(self):
                self.calls = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, _sql):
                self.calls += 1
                return self

            def fetchall(self):
                return [
                    ("routing:fast:success", 3, 10.0),
                    ("routing:fast:failure", 1, 30.0),
                    ("routing:slow:failure", 2, 50.0),
                ]

        class FakeDB:
            def __init__(self):
                self.connection = FakeConnection()

            def get_connection(self):
                return self.connection

        db = FakeDB()
        processor = SimpleNamespace(
            history_service=SimpleNamespace(repo=SimpleNamespace(db=db))
        )

        snapshot = routing_feedback_snapshot(processor)

        self.assertEqual(db.connection.calls, 1)
        self.assertEqual(snapshot["fast"]["samples"], 4)
        self.assertAlmostEqual(snapshot["fast"]["success_rate"], 0.75)
        self.assertAlmostEqual(snapshot["fast"]["avg_duration_ms"], 15.0)
        self.assertEqual(snapshot["slow"]["samples"], 2)
        self.assertEqual(snapshot["slow"]["success_rate"], 0.0)

    def test_flow_transform_projects_filters_and_aggregates_without_code_execution(self):
        data = [{"name": "a", "kind": "x", "noise": 1},
                {"name": "b", "kind": "y", "noise": 2},
                {"name": "a", "kind": "x", "noise": 1}]
        projected = _transform(data, {"where": {"kind": "x"}, "project": ["name"], "unique": True})
        self.assertEqual(projected, [{"name": "a"}])
        self.assertEqual(_transform(projected, {"aggregate": "count"}), 1)

    def test_admission_gate_rejects_material_regression(self):
        baseline = AgentScorecard(.8, .8, .8, .8)
        candidate = AgentScorecard(.85, .9, .70, .9)
        result = AgentAdmissionGate(max_regression=.02).evaluate(baseline, candidate)
        self.assertFalse(result.accepted)
        self.assertIn("reliability", result.regressions)

    def test_verification_rejects_invalid_python_before_project_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            report = VerificationOrchestrator(root).verify(["broken.py"])
            self.assertFalse(report.ok)
            self.assertTrue(any(d.validator == "python.ast" and not d.ok for d in report.diagnostics))

    def test_turn_journal_persists_operational_state_in_canonical_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = HistoryDatabase(tmp, in_memory=True)
            repo = HistoryRepository(db)
            identity = resolve_workspace_identity(db, tmp)
            conv = repo.create_conversation(identity.id, "journal")
            history = SimpleNamespace(repo=repo, workspace_id=identity.id)
            processor = SimpleNamespace(
                history_service=history,
                _workspace_id=identity.id,
                session_state=SessionState(),
                _emit=lambda *args, **kwargs: None,
            )
            processor.workspace_id = identity.id
            cmd = TurnCommand(conversation_id=conv["id"], prompt="do work", turn_id="journal-turn")
            journal = DurableTurnJournal(processor)
            journal.begin(cmd)
            journal.state(cmd, "RETRIEVING")
            row = _turn(processor, cmd.turn_id)
            self.assertEqual(row["state"], "RETRIEVING")
            journal.state(cmd, "COMPLETED")
            row = _turn(processor, cmd.turn_id)
            self.assertEqual(row["state"], "COMPLETED")
            self.assertIsNotNone(row["completed_at"])
            db.close()


if __name__ == "__main__":
    unittest.main()
