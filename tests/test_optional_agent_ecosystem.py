"""Regression tests for bounded setup inspection and explicit Strix delegation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from kitt.setup_advisor import recommend
from kitt.strix_integration import scan_strix, strix_status


class SetupAdvisorTests(unittest.TestCase):
    def test_detects_java_frontend_without_exec_or_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pom.xml").write_text("<artifactId>spring-boot-starter</artifactId>")
            (root / "package.json").write_text(json.dumps({
                "dependencies": {"@angular/core": "20.0.0"}
            }))
            result = recommend(root)
            self.assertIn("spring-boot", result["signals"])
            self.assertIn("frontend", result["signals"])
            self.assertTrue(result["read_only"])
            self.assertTrue(any(item["name"] == "kitt-memoryd" for item in result["recommendations"]))
            self.assertFalse((root / ".kitt").exists())

    def test_ignores_symlinked_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").symlink_to(root / "not-present.json")
            self.assertNotIn("node", recommend(root)["signals"])


class StrixTests(unittest.TestCase):
    def test_missing_authorization_never_executes(self):
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run") as runner:
            with self.assertRaises(PermissionError):
                scan_strix(workspace=tmp, target=".", authorized=False)
            runner.assert_not_called()

    def test_installed_binary_executes_with_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("shutil.which", return_value="/usr/bin/strix"), patch(
                "subprocess.run", return_value=Mock(returncode=0)
            ) as runner:
                self.assertEqual(
                    scan_strix(workspace=tmp, target=".", authorized=True), 0
                )
                self.assertEqual(runner.call_args.args[0][:2], ["/usr/bin/strix", "--target"])
                self.assertIs(runner.call_args.kwargs["shell"], False)

    def test_escaping_workspace_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            with patch("shutil.which", return_value="/usr/bin/strix"):
                with self.assertRaises(PermissionError):
                    scan_strix(workspace=workspace, target="..", authorized=True)

    def test_url_credentials_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("shutil.which", return_value="/usr/bin/strix"):
                with self.assertRaises(ValueError):
                    scan_strix(
                        workspace=tmp, target="https://user:secret@example.com",
                        authorized=True,
                    )


if __name__ == "__main__":
    unittest.main()
