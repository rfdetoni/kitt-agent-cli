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
                    project_state = workspace.resolve(strict=False) / ".kitt"
                    self.assertTrue(project_state.is_dir())
                    self.assertEqual(
                        Path(runtime.database.db_path).resolve(strict=False),
                        (project_state / "history" / "history.sqlite3").resolve(
                            strict=False
                        ),
                    )
                    self.assertEqual(
                        Path(runtime.repository_index.db_path).resolve(strict=False),
                        (project_state / "index" / "index.db").resolve(strict=False),
                    )
                    self.assertEqual(
                        runtime.working_set.path.resolve(strict=False),
                        (project_state / "working_set.json").resolve(strict=False),
                    )
                    self.assertEqual(
                        runtime.artifacts.storage.resolve(strict=False),
                        (project_state / "artifacts").resolve(strict=False),
                    )
                finally:
                    runtime.close()

    def test_workspace_identity_uses_project_history_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "project"
            workspace.mkdir(parents=True)
            canonical_workspace = workspace.resolve(strict=False)

            identity = WorkspaceIdentity.build(workspace)

            self.assertEqual(identity.canonical_root, canonical_workspace)
            self.assertTrue(
                (
                    canonical_workspace
                    / ".kitt"
                    / "history"
                    / "history.sqlite3"
                ).is_file()
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
