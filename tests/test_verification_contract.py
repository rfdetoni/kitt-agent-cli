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
