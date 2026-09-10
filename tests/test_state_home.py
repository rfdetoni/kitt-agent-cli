from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.core.runtime import KittRuntime
from kitt.core.workspace_identity import WorkspaceIdentity
from kitt.security.private_state import workspace_key


class _StopRuntimeBuild(RuntimeError):
    pass


class StateHomeTests(unittest.TestCase):
    def test_runtime_uses_workspace_database_without_checkout_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            workspace = root / "Desktop" / "project"
            home.mkdir(parents=True)
            workspace.mkdir(parents=True)
            captured: dict[str, Path] = {}

            def stop_after_workspace_database(workspace_root, *args, **kwargs):
                captured["root"] = Path(workspace_root)
                raise _StopRuntimeBuild

            with (
                patch("kitt.core.runtime.Path.home", return_value=home),
                patch(
                    "kitt.core.runtime.WorkspaceHistoryDatabase",
                    side_effect=stop_after_workspace_database,
                ),
                self.assertRaises(_StopRuntimeBuild),
            ):
                KittRuntime.build(str(workspace))

            self.assertEqual(captured["root"], workspace.resolve())
            self.assertFalse((workspace / ".kitt").exists())

    def test_workspace_identity_persists_database_under_home_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            workspace = root / "Desktop" / "project"
            home.mkdir(parents=True)
            workspace.mkdir(parents=True)

            with patch("kitt.security.private_state.Path.home", return_value=home):
                identity = WorkspaceIdentity.build(workspace)

            expected_db = (
                home
                / ".kitt"
                / "workspaces"
                / workspace_key(workspace)
                / "history"
                / "history.sqlite3"
            )
            self.assertEqual(identity.canonical_root, workspace.resolve())
            self.assertTrue(expected_db.is_file())
            self.assertFalse((workspace / ".kitt").exists())


if __name__ == "__main__":
    unittest.main()
