from __future__ import annotations

import json
import tempfile
from pathlib import Path

from kitt.tools.build_detector import VerificationStep
from kitt.validation.contract import VerificationContractManager


def test_project_contract_can_disable_known_step_and_bound_timeout():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".kitt").mkdir()
        (root / ".kitt" / "verification.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "steps": {
                        "java.tests": {"enabled": False},
                        "java.compile": {"timeout_seconds": 9999},
                        "unknown.command": {"enabled": True},
                    },
                }
            ),
            encoding="utf-8",
        )
        global_path = root / "private" / "baselines.json"
        manager = VerificationContractManager(root, global_path=global_path)
        steps = manager.apply_plan(
            [
                VerificationStep("java.compile", ["mvn", "compile"], 180, "compile"),
                VerificationStep("java.tests", ["mvn", "test"], 240, "test"),
                VerificationStep("unknown.command", ["sh", "-c", "unsafe"], 10, "test"),
            ]
        )
        assert [step.name for step in steps] == ["java.compile"]
        assert steps[0].timeout_seconds == 600


def test_global_baseline_is_created_and_reused():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        global_path = root / "private" / "baselines.json"
        manager = VerificationContractManager(root, global_path=global_path)
        first = manager.effective()
        second = VerificationContractManager(root, global_path=global_path).effective()
        assert first["steps"]["go.tests"]["enabled"] is True
        assert second["steps"]["go.tests"]["timeout_seconds"] == 240
        assert global_path.exists()


def test_full_node_verification_runs_available_build_for_directory_targets():
    from types import SimpleNamespace
    from kitt.validation.orchestrator import VerificationOrchestrator
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "src" / "bad.ts").write_text('const count: number = "wrong";')
        (root / "package.json").write_text(json.dumps({"scripts": {"build": "tsc --noEmit"}}))
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=1, stdout="", stderr="TS2322", timed_out=False, cancelled=False)
        runner = SimpleNamespace(run=run)
        report = VerificationOrchestrator(root, runner).verify(["src"], force_full=True)
        assert not report.ok
        assert calls == [["npm", "run", "-s", "build"]]
        assert report.steps[0].name == "node.build"
        assert "TS2322" in report.failure_message()


def test_node_check_alias_and_duplicate_build_are_not_silently_omitted():
    from kitt.tools.build_detector import BuildDetector
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "package.json").write_text(json.dumps({"scripts": {"check": "tsc --noEmit", "build": "tsc --noEmit"}}))
        assert [step.name for step in BuildDetector(tmp).plan_verification(["a.ts"], full=True)] == ["node.check"]
