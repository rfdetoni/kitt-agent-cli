import unittest
import tempfile
from pathlib import Path
from kitt.tools.build_detector import BuildDetector
from kitt.tools.log_reducer import LogReducer

class TestPhase4Validation(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.detector = BuildDetector(root_dir=self.tmp_dir.name)
        self.reducer = LogReducer()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_build_detector_python(self):
        (self.root_path / "tests").mkdir()
        cmd = self.detector.detect_test_command()
        self.assertIsNotNone(cmd)
        self.assertIn("unittest", cmd)

    def test_log_reducer_strips_noise_preserves_errors(self):
        raw_log = """
Downloading http://repo.maven.org/maven2/foo.jar
Progress (10): 100%
[INFO] --- maven-compiler-plugin:3.8.1:compile ---
ERROR: Failed to compile src/Main.java
File "app.py", line 42, in test_func
AssertionError: Expected 1 got 0
"""
        reduced = self.reducer.reduce_log(raw_log)
        self.assertNotIn("Downloading http", reduced)
        self.assertNotIn("Progress (10)", reduced)
        self.assertIn("ERROR: Failed to compile src/Main.java", reduced)
        self.assertIn("AssertionError", reduced)

if __name__ == '__main__':
    unittest.main()
