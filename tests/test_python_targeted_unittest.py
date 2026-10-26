from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from kitt.tools.build_detector import BuildDetector


def test_python_targeted_finds_sibling_tests_without_pytest():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        nested = root / "reverse-engineering"
        nested.mkdir()
        (nested / "validate_elf.py").write_text("value=1\n")
        (nested / "test_validate_elf.py").write_text("import unittest\nclass TestElf(unittest.TestCase):\n    def test_valid(self):\n        self.assertTrue(True)\n")
        detector = BuildDetector(directory)
        with patch("kitt.tools.build_detector.importlib.util.find_spec", return_value=None):
            steps = detector.plan_verification(["reverse-engineering/validate_elf.py"])
            assert len(steps) == 1
            assert steps[0].name == "python.targeted-tests"
            assert steps[0].argv == [
                "python3", "-m", "unittest", "discover",
                "-s", "reverse-engineering", "-p", "test_validate_elf.py", "-v",
            ]
            run = subprocess.run(
                steps[0].argv, cwd=root, text=True,
                capture_output=True, timeout=10, check=False,
            )
            assert run.returncode == 0, run.stderr
            assert "Ran 1 test" in run.stderr
            direct = detector.plan_verification(["reverse-engineering/test_validate_elf.py"])
            assert direct[0].argv == steps[0].argv
        with patch("kitt.tools.build_detector.importlib.util.find_spec", return_value=object()):
            steps = detector.plan_verification(["reverse-engineering/validate_elf.py"])
            assert steps[0].argv == [
                "python3", "-m", "pytest", "-q", "reverse-engineering/test_validate_elf.py",
            ]


def test_host_required_python_targeted_tests_are_available_in_full_mode():
    """Regression: check_ids once forced full=True and discarded sibling tests."""
    from types import SimpleNamespace
    from kitt.validation.orchestrator import VerificationOrchestrator

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "validate_elf.py").write_text("VALUE = 42\n")
        (root / "test_validate_elf.py").write_text(
            "import unittest\n"
            "class TestInventory(unittest.TestCase):\n"
            "    def test_sanity(self): self.assertEqual(42, 42)\n"
        )
        calls = []
        def run(argv, **kwargs):
            calls.append(list(argv))
            return SimpleNamespace(
                returncode=0, stdout="Ran 1 test OK", stderr="",
                timed_out=False, cancelled=False,
            )
        with patch("kitt.tools.build_detector.importlib.util.find_spec", return_value=None):
            verifier = VerificationOrchestrator(
                root, SimpleNamespace(run=run),
                authorize_step=lambda step: "ALLOW",
            )
            report = verifier.verify(
                ["validate_elf.py"], check_ids=["python.targeted-tests"]
            )
        assert report.ok, report.failure_message()
        assert [s.name for s in report.steps] == ["python.targeted-tests"]
        assert calls == [[
            "python3", "-m", "unittest", "discover",
            "-s", ".", "-p", "test_validate_elf.py", "-v",
        ]]


def test_required_python_targeted_tests_without_test_remain_unavailable():
    from types import SimpleNamespace
    from kitt.validation.orchestrator import VerificationOrchestrator

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "validate_elf.py").write_text("VALUE = 42\n")
        calls = []
        verifier = VerificationOrchestrator(
            root, SimpleNamespace(run=lambda *args, **kwargs: calls.append(args))
        )
        report = verifier.verify(
            ["validate_elf.py"], check_ids=["python.targeted-tests"]
        )
        assert not report.ok
        assert report.status == "UNAVAILABLE"
        assert "python.targeted-tests" in report.failure_message()
        assert calls == []
