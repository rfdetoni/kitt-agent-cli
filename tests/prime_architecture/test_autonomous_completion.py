import unittest
from types import SimpleNamespace

from kitt.goals.completion import (
    COMPLETION_REPORT_PREFIX,
    AutonomousCompletionEngine,
)


class _Result:
    def __init__(self, returncode=0, stdout="ok", stderr="", timed_out=False, cancelled=False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.cancelled = cancelled


class _Runner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, argv, timeout_seconds=120):
        self.calls.append((list(argv), timeout_seconds))
        return self.result


def _goal(criteria=None, gates=None):
    return SimpleNamespace(
        id="goal_test",
        objective="Implement feature",
        success_criteria=list(criteria or []),
        gates=list(gates or []),
    )


class TestAutonomousCompletionEngine(unittest.TestCase):
    def test_no_contract_preserves_legacy_success(self):
        verification = AutonomousCompletionEngine().verify(_goal(), "done")
        self.assertTrue(verification.success)
        self.assertEqual(verification.score, 1.0)

    def test_requires_machine_readable_evidence_for_semantic_criteria(self):
        criterion = "Cache invalidation is tested"
        engine = AutonomousCompletionEngine()
        failed = engine.verify(_goal([criterion]), "Implementation complete")
        self.assertFalse(failed.success)
        self.assertIn("Missing or invalid", failed.feedback)

        response = (
            "Implemented and validated.\n"
            f'{COMPLETION_REPORT_PREFIX} '
            '{"status":"SUCCEEDED","criteria":['
            '{"criterion":"Cache invalidation is tested","satisfied":true,'
            '"evidence":"pytest tests/test_cache.py passed"}],'
            '"summary":"done"}'
        )
        passed = engine.verify(_goal([criterion]), response)
        self.assertTrue(passed.success)
        self.assertEqual(passed.score, 1.0)

    def test_quality_gate_is_authoritative(self):
        gate = SimpleNamespace(
            id="gate_1",
            name="Unit tests",
            argv=["python", "-m", "pytest", "-q"],
            timeout_seconds=30,
        )
        recorded = []
        runner = _Runner(_Result(returncode=1, stdout="1 failed"))
        engine = AutonomousCompletionEngine(
            gate_runner=runner,
            gate_result_recorder=lambda gate_id, **kwargs: recorded.append((gate_id, kwargs)),
        )
        verification = engine.verify(_goal(gates=[gate]), "done")
        self.assertFalse(verification.success)
        self.assertEqual(runner.calls, [(["python", "-m", "pytest", "-q"], 30)])
        self.assertEqual(recorded[0][0], "gate_1")
        self.assertEqual(recorded[0][1]["status"], "FAILED")
        self.assertIn("exit_code=1", verification.feedback)


    def test_gate_never_bypasses_policy_denial(self):
        gate = SimpleNamespace(
            id="gate_1",
            name="Tests",
            argv=["pytest", "-q"],
            timeout_seconds=30,
        )
        runner = _Runner(_Result(returncode=0))
        engine = AutonomousCompletionEngine(
            gate_runner=runner,
            gate_authorizer=lambda argv: "DENY",
        )
        verification = engine.verify(_goal(gates=[gate]), "done")
        self.assertFalse(verification.success)
        self.assertEqual(runner.calls, [])
        self.assertIn("PolicyEngine returned DENY", verification.feedback)

    def test_stagnation_is_detected_after_repeated_failure(self):
        engine = AutonomousCompletionEngine(stagnation_threshold=2)
        verification = engine.verify(_goal(["criterion"]), "no report")
        first = engine.next_state(None, verification)
        second = engine.next_state(first, verification)
        self.assertFalse(first["stagnated"])
        self.assertTrue(second["stagnated"])
        prompt = engine.build_execution_prompt(_goal(["criterion"]), "task", second)
        self.assertIn("STAGNATION DETECTED", prompt)
        self.assertIn("materially different", prompt)

    def test_success_requires_all_gates_and_all_criteria(self):
        gate = SimpleNamespace(
            id="gate_1",
            name="Build",
            argv=["python", "-m", "compileall", "-q", "kitt"],
            timeout_seconds=30,
        )
        engine = AutonomousCompletionEngine(gate_runner=_Runner(_Result(returncode=0)))
        response = (
            f'{COMPLETION_REPORT_PREFIX} '
            '{"status":"SUCCEEDED","criteria":['
            '{"criterion":"Build passes","satisfied":true,"evidence":"compileall passed"},'
            '{"criterion":"Tests pass","satisfied":false,"evidence":"one test failed"}]}'
        )
        verification = engine.verify(_goal(["Build passes", "Tests pass"], [gate]), response)
        self.assertFalse(verification.success)
        self.assertAlmostEqual(verification.score, 2 / 3)
        self.assertIn("Tests pass", verification.feedback)


if __name__ == "__main__":
    unittest.main()
