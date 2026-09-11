from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.core.runtime import KittRuntime
from kitt.core.workspace_identity import WorkspaceIdentity


class ProjectStateLocationTests(unittest.TestCase):
    def test_runtime_keeps_operational_state_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            workspace = root / "Desktop" / "project"
            home.mkdir(parents=True)
            workspace.mkdir(parents=True)

            with patch("pathlib.Path.home", return_value=home):
                runtime = KittRuntime.build(str(workspace))
                try:
                    project_state = workspace / ".kitt"
                    self.assertTrue(project_state.is_dir())
                    self.assertEqual(
                        Path(runtime.database.db_path),
                        project_state / "history" / "history.sqlite3",
                    )
                    self.assertEqual(
                        Path(runtime.repository_index.db_path),
                        project_state / "index" / "index.db",
                    )
                    self.assertEqual(
                        runtime.working_set.path,
                        project_state / "working_set.json",
                    )
                    self.assertEqual(
                        runtime.artifacts.storage,
                        project_state / "artifacts",
                    )
                finally:
                    runtime.close()

    def test_workspace_identity_uses_project_history_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "project"
            workspace.mkdir(parents=True)

            identity = WorkspaceIdentity.build(workspace)

            self.assertEqual(identity.canonical_root, workspace.resolve())
            self.assertTrue(
                (workspace / ".kitt" / "history" / "history.sqlite3").is_file()
            )

    def test_global_state_remains_separate_from_project_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            workspace = root / "project"
            home.mkdir(parents=True)
            workspace.mkdir(parents=True)

            with patch("pathlib.Path.home", return_value=home):
                runtime = KittRuntime.build(str(workspace))
                runtime.close()

            self.assertTrue((workspace / ".kitt").is_dir())
            self.assertTrue((home / ".kitt").is_dir())
            self.assertFalse((home / ".kitt" / "history").exists())
            self.assertFalse((home / ".kitt" / "index").exists())


if __name__ == "__main__":
    unittest.main()
