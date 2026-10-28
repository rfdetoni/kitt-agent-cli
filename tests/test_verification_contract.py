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

def test_c_code_requires_real_build_not_only_structural_pass():
    from types import SimpleNamespace
    from kitt.validation.orchestrator import VerificationOrchestrator
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "dlls" / "win32u").mkdir(parents=True)
        (root / "dlls" / "win32u" / "source.c").write_text("int answer(void) { return 42; }\n")
        runner = SimpleNamespace(run=lambda argv, **kwargs: SimpleNamespace(
            returncode=0, stdout="built", stderr="", timed_out=False, cancelled=False,
        ))
        orchestrator = VerificationOrchestrator(root, runner)
        report = orchestrator.verify(["dlls/win32u/source.c"])
        assert not report.ok
        assert report.status == "UNAVAILABLE"
        assert "configured Makefile" in report.failure_message()

        (root / "Makefile").write_text("all:\n\t@true\n")
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(
                returncode=0, stdout="compiled", stderr="", timed_out=False, cancelled=False,
            )
        orchestrator = VerificationOrchestrator(root, SimpleNamespace(run=run))
        report = orchestrator.verify(["dlls/win32u/source.c"])
        assert report.ok, report.failure_message()
        assert [s.name for s in report.steps] == ["c.build"]
        assert calls == [["make", "-C", ".", "-j2"]]


def test_required_python_lint_verifies_workspace_after_docs_only_edit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from kitt.tools.build_detector import BuildDetector
    from kitt.validation.orchestrator import VerificationOrchestrator

    (tmp_path / "validate_elf.py").write_text("value = 1\\n")
    docs = tmp_path / "analysis"
    docs.mkdir()
    (docs / "baseline.md").write_text("# Baseline\\n")
    monkeypatch.setattr(BuildDetector, "ruff_executable", staticmethod(lambda: "ruff"))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="", timed_out=False, cancelled=False)

    report = VerificationOrchestrator(
        tmp_path, SimpleNamespace(run=run)
    ).verify(["analysis/baseline.md"], check_ids=["python.lint"])
    assert report.ok, report.failure_message()
    assert report.workspace_verified
    assert calls == [["ruff", "check", "."]]
    assert [step.name for step in report.steps] == ["python.lint"]


def test_required_lint_fails_closed_when_ruff_is_unavailable(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from kitt.tools.build_detector import BuildDetector
    from kitt.validation.orchestrator import VerificationOrchestrator

    (tmp_path / "validate_elf.py").write_text("value = 1\\n")
    (tmp_path / "baseline.md").write_text("# Baseline\\n")
    monkeypatch.setattr(BuildDetector, "ruff_executable", staticmethod(lambda: None))
    runner = SimpleNamespace(run=lambda argv, **kwargs: (_ for _ in ()).throw(
        AssertionError("No lint should run without a linter")
    ))
    report = VerificationOrchestrator(tmp_path, runner).verify(
        ["baseline.md"], check_ids=["python.lint"]
    )
    assert not report.ok
    assert report.status == "UNAVAILABLE"
    assert "python.lint" in report.command_output


def test_ruff_discovery_uses_kitt_venv_even_when_path_is_unset(tmp_path, monkeypatch):
    import sys
    from kitt.tools.build_detector import BuildDetector

    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    binary = binary_dir / "ruff"
    binary.write_text("#!/bin/sh\\nexit 0\\n")
    binary.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(binary_dir / "python"))
    monkeypatch.setattr("kitt.tools.build_detector.shutil.which", lambda command: None)
    assert BuildDetector.ruff_executable() == str(binary)


def test_required_targeted_tests_run_workspace_suite_for_doc_only_change(tmp_path):
    from types import SimpleNamespace
    from kitt.validation.orchestrator import VerificationOrchestrator

    (tmp_path / "test_validate_elf.py").write_text(
        "import unittest\\nclass Check(unittest.TestCase):\\n    def test_ok(self): assert True\\n"
    )
    (tmp_path / "baseline.md").write_text("# evidence\\n")
    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        return SimpleNamespace(returncode=0, stdout="1 test passed", stderr="",
                               timed_out=False, cancelled=False)

    report = VerificationOrchestrator(
        tmp_path, SimpleNamespace(run=run)
    ).verify(["baseline.md"], check_ids=["python.targeted-tests"])
    assert report.ok, report.failure_message()
    assert report.workspace_verified
    assert commands == [["python3", "-m", "unittest", "-v", "test_validate_elf"]]


def test_required_targeted_tests_fail_closed_when_workspace_has_no_tests(tmp_path):
    from types import SimpleNamespace
    from kitt.validation.orchestrator import VerificationOrchestrator

    (tmp_path / "baseline.md").write_text("# evidence\\n")
    runner = SimpleNamespace(run=lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("No test may run without an existing test file")
    ))
    report = VerificationOrchestrator(tmp_path, runner).verify(
        ["baseline.md"], check_ids=["python.targeted-tests"]
    )
    assert not report.ok
    assert report.status == "UNAVAILABLE"
    assert "python.targeted-tests" in report.command_output
