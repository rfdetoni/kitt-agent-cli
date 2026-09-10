from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.core.runtime import KittRuntime
from kitt.core.workspace_identity import WorkspaceIdentity


class _StopRuntimeBuild(RuntimeError):
    pass


class StateHomeTests(unittest.TestCase):
    def test_runtime_defaults_state_root_to_user_home_not_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            workspace = root / "Desktop" / "project"
            home.mkdir(parents=True)
            workspace.mkdir(parents=True)
            captured: dict[str, Path] = {}

            def stop_after_state_root(root_dir, *args, **kwargs):
                captured["root"] = Path(root_dir)
                raise _StopRuntimeBuild

            with (
                patch("kitt.core.runtime.Path.home", return_value=home),
                patch(
                    "kitt.core.runtime.HistoryDatabase",
                    side_effect=stop_after_state_root,
                ),
                self.assertRaises(_StopRuntimeBuild),
            ):
                KittRuntime.build(str(workspace))

            self.assertEqual(captured["root"], home.resolve())
            self.assertFalse((workspace / ".kitt").exists())

    def test_workspace_identity_persists_database_under_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            workspace = root / "Desktop" / "project"
            home.mkdir(parents=True)
            workspace.mkdir(parents=True)

            with patch("kitt.core.workspace_identity.Path.home", return_value=home):
                identity = WorkspaceIdentity.build(workspace)

            self.assertEqual(identity.canonical_root, workspace.resolve())
            self.assertTrue((home / ".kitt" / "history" / "history.sqlite3").is_file())
            self.assertFalse((workspace / ".kitt").exists())


if __name__ == "__main__":
    unittest.main()
