import tempfile
import unittest
from pathlib import Path

from kitt.index.scanner import RepositoryScanner


class RepositoryScannerLogIgnoreTests(unittest.TestCase):
    def test_code_index_ignores_log_files_but_keeps_source(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "runtime.log").write_text("volatile trace\n", encoding="utf-8")
            (root / "kitt-agent-full.log").write_text("agent trace\n", encoding="utf-8")

            files = RepositoryScanner(root).scan_relative_files()

            self.assertIn("src/app.py", files)
            self.assertNotIn("runtime.log", files)
            self.assertNotIn("kitt-agent-full.log", files)


if __name__ == "__main__":
    unittest.main()
