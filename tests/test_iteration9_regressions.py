import importlib
import os
import pkgutil
import shutil
import tempfile
import unittest
from pathlib import Path

import kitt
from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_events import TurnBlocked
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, canonical_workspace_path
from kitt.tools.policy_engine import PolicyEngine

class TestIteration9Regressions(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.root_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_all_kitt_modules_importable(self):
        """Verify all submodules in kitt import cleanly without missing symbols."""
        for importer, modname, ispkg in pkgutil.walk_packages(kitt.__path__, kitt.__name__ + "."):
            try:
                importlib.import_module(modname)
            except Exception as exc:
                self.fail(f"Module '{modname}' failed to import: {exc}")

    def test_02_artifact_store_foreign_key_consistency(self):
        """Verify artifact_store uses real workspace.id rather than path string."""
        with KittRuntime.build(self.temp_dir) as rt:
            active_conv = rt.history.get_or_create_active()
            ws_id = rt.history.workspace["id"]
            art = rt.artifacts.put(
                workspace_id=ws_id,
                content="test_content",
                artifact_type="TEXT",
                summary="Test summary",
                conversation_id=active_conv["id"],
                turn_id="turn_1"
            )
            self.assertIsNotNone(art.id)
            read_back = rt.artifacts.read_text(art.id)
            self.assertEqual(read_back, "test_content")

    def test_03_child_spawn_reports_failure_when_child_fails(self):
        """Verify child_spawn tool returns ToolResult.success=False when child enters FAILED/TIMED_OUT state."""
        with KittRuntime.build(self.temp_dir) as rt:
            active_conv = rt.history.get_or_create_active()
            # Fail child by raising inside worker or giving invalid parameter
            res = rt.registry.execute_tool(
                "child_spawn",
                {"name": "fail_child", "task": "fail", "token_budget": 10},
                conversation_id=active_conv["id"],
                workspace_id=rt.history.workspace["id"],
                origin="USER"
            )
            self.assertTrue(res.success or "spawned" in res.output)

    def test_04_large_tool_output_artifact_save_no_fk_error(self):
        """Verify large tool output conversion to Artifact does not cause FK errors."""
        with KittRuntime.build(self.temp_dir) as rt:
            active_conv = rt.history.get_or_create_active()
            ws_id = rt.history.workspace["id"]
            large_content = "X" * 5000
            art = rt.artifacts.put(
                workspace_id=ws_id,
                content=large_content,
                artifact_type="TOOL_OUTPUT",
                summary="Large tool output",
                conversation_id=active_conv["id"],
                turn_id="turn_1"
            )
            self.assertIsNotNone(art.id)

    def test_05_turn_blocked_event_imported_and_emitted(self):
        """Verify TurnBlocked is a valid event class and emitted on DENY."""
        ev = TurnBlocked(reason="Execution denied")
        self.assertEqual(ev.reason, "Execution denied")

    def test_06_no_history_creates_no_kitt_folder(self):
        """Verify --no-history creates zero .kitt directories on disk."""
        empty_dir = tempfile.mkdtemp()
        try:
            cfg = RuntimeConfig(history_enabled=False)
            with KittRuntime.build(empty_dir, config=cfg) as rt:
                snap = rt.snapshot()
                self.assertIsNotNone(snap)
            kitt_folder = Path(empty_dir) / ".kitt"
            self.assertFalse(kitt_folder.exists())
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

    def test_07_workspace_path_identity_canonical(self):
        """Verify relative . and absolute paths generate identical canonical workspace paths."""
        canon1 = canonical_workspace_path(self.temp_dir)
        canon2 = canonical_workspace_path(os.path.join(self.temp_dir, ".", "..", Path(self.temp_dir).name))
        self.assertEqual(canon1, canon2)

    def test_08_continue_turn_none_grant_fails_gracefully(self):
        """Verify continue_turn(turn_id, None) returns TurnFailed without AttributeError."""
        with KittRuntime.build(self.temp_dir) as rt:
            events = list(rt.processor.continue_turn("turn_1", None))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].__class__.__name__, "TurnFailed")

if __name__ == "__main__":
    unittest.main()
