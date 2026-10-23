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
