from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.daemon.protocol import DaemonEvent
from kitt.core.turn_processor import TurnProcessor
from kitt.daemon.redaction import sanitize_public_event_payload
from kitt.domain.entities import EditBlock
from kitt.edit_format.applier import DiffApplier
from kitt.edit_format.changeset import ChangeSetTracker, FileSnapshot
from kitt.history.database import HistoryDatabase
from kitt.index.scanner import RepositoryScanner
from kitt.security.workspace_fs import WorkspaceFileSystem
from kitt.tools.approval import ApprovalManager
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.registry import ToolRegistry
from kitt.tools.handlers import ToolContext
from kitt.tools.handlers.files import WriteFileHandler
from kitt.ui.daemon_bridge import DaemonUIBridge


class FinalMainRemediationTests(unittest.TestCase):
    def test_session_remembered_approval_never_leaks_to_other_session(self):
        approval = ApprovalManager()
        approval.remember(
            "write_file", "**", "allow", "session", conversation_id="session-a"
        )
        self.assertEqual(
            approval.check_remembered("write_file", "src/a.py", "session-a"), "allow"
        )
        self.assertIsNone(
            approval.check_remembered("write_file", "src/a.py", "session-b")
        )
        with self.assertRaises(ValueError):
            approval.remember("write_file", "**", "allow", "session")

    def test_clear_remembered_all_removes_session_and_workspace_rules(self):
        approval = ApprovalManager()
        approval.remember(
            "write_file", "**", "allow", "session", conversation_id="session-a"
        )
        approval.remember("apply_patch", "**", "allow", "workspace")
        self.assertEqual(approval.clear_remembered(scope="all"), 2)
        self.assertIsNone(
            approval.check_remembered("write_file", "x.py", "session-a")
        )
        self.assertIsNone(
            approval.check_remembered("apply_patch", "x.py", "session-a")
        )

    def test_policy_uses_conversation_when_resolving_remembered_rule(self):
        approval = ApprovalManager()
        approval.remember(
            "write_file", "**", "allow", "session", conversation_id="session-a"
        )
        policy = PolicyEngine(approval_manager=approval)
        self.assertEqual(
            policy.evaluate_tool(
                "write_file", {"path": "x.py"}, conversation_id="session-a"
            ),
            "ALLOW",
        )
        self.assertNotEqual(
            policy.evaluate_tool(
                "write_file", {"path": "x.py"}, conversation_id="session-b"
            ),
            "ALLOW",
        )

    def test_public_event_redacts_secrets_and_reasoning_before_journal(self):
        payload = {
            "prompt": "Authorization: Bearer very-secret-token-123456789",
            "nested": {
                "token": "opaque-short-token",
                "refresh_token": "opaque-refresh-value",
                "api_key": "short-api-secret",
                "tokens": 17,
                "input_tokens": 9,
            },
            "thought": "private reasoning",
        }
        clean = sanitize_public_event_payload("ThinkingCompleted", payload)
        rendered = repr(clean)
        self.assertNotIn("very-secret-token", rendered)
        self.assertNotIn("opaque-short-token", rendered)
        self.assertNotIn("opaque-refresh-value", rendered)
        self.assertNotIn("short-api-secret", rendered)
        self.assertNotIn("private reasoning", rendered)
        self.assertNotIn("thought", clean)
        self.assertEqual(clean["nested"]["tokens"], 17)
        self.assertEqual(clean["nested"]["input_tokens"], 9)
        self.assertIn("REDACTED", rendered)

    def test_atomic_write_expected_absent_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            fs = WorkspaceFileSystem(tmp)
            fs.atomic_write("new.txt", "one", expected_exists=False)
            with self.assertRaises(ValueError):
                fs.atomic_write("new.txt", "two", expected_exists=False)
            self.assertEqual((Path(tmp) / "new.txt").read_text(), "one")

    def test_multifile_patch_failure_rolls_back_prior_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("A0", encoding="utf-8")
            (root / "b.txt").write_text("B0", encoding="utf-8")
            applier = DiffApplier(ChangeSetTracker(tmp))
            original = WorkspaceFileSystem.atomic_write

            def fail_b(self, rel, content, **kwargs):
                if str(rel) == "b.txt" and content == "B1":
                    raise OSError("synthetic second-file failure")
                return original(self, rel, content, **kwargs)

            blocks = [
                EditBlock("a.txt", "A0", "A1"),
                EditBlock("b.txt", "B0", "B1"),
            ]
            with patch.object(WorkspaceFileSystem, "atomic_write", fail_b):
                result = applier.apply(
                    blocks, tmp, workspace_id="ws", conversation_id="s1", turn_id="t1"
                )
            self.assertFalse(result.success)
            self.assertEqual((root / "a.txt").read_text(), "A0")
            self.assertEqual((root / "b.txt").read_text(), "B0")

    def test_multiple_blocks_for_same_file_are_staged_then_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("one two three", encoding="utf-8")
            applier = DiffApplier(ChangeSetTracker(tmp))
            result = applier.apply(
                [
                    EditBlock("a.txt", "one", "ONE"),
                    EditBlock("a.txt", "two", "TWO"),
                ],
                tmp,
                workspace_id="ws",
                conversation_id="s1",
                turn_id="t1",
            )
            self.assertTrue(result.success, result.errors)
            self.assertEqual((root / "a.txt").read_text(), "ONE TWO three")

    def test_turn_processor_and_registry_share_one_canonical_applier(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(tmp)
            processor = TurnProcessor(tmp, registry=registry)
            self.assertIs(processor.diff_applier, registry.applier)
            self.assertIs(processor.diff_applier.tracker, registry.applier.tracker)

    def test_write_file_uses_canonical_workspace_tracker_and_is_undoable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = HistoryDatabase(in_memory=True)
            registry = ToolRegistry(tmp)
            registry.attach_services(db=db)
            self.assertEqual(registry.applier.tracker.root_dir, root.resolve())
            ctx = ToolContext(
                registry=registry, turn_id="t1", conversation_id="session-a",
                workspace_id="ws", origin="MODEL",
            )
            result = WriteFileHandler().execute(
                {"path": "nested/new.txt", "content": "hello"}, ctx
            )
            self.assertTrue(result.success, result.error)
            self.assertEqual((root / "nested" / "new.txt").read_text(), "hello")
            registry.applier.tracker.revert_last_changeset("session-a", "ws")
            self.assertFalse((root / "nested" / "new.txt").exists())
            db.close()

    def test_undo_refuses_to_overwrite_external_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("A0", encoding="utf-8")
            db = HistoryDatabase(in_memory=True)
            tracker = ChangeSetTracker(tmp, db=db, workspace_id="ws")
            applier = DiffApplier(tracker)
            self.assertTrue(applier.apply(
                [EditBlock("a.txt", "A0", "A1")], tmp,
                workspace_id="ws", conversation_id="session-a", turn_id="t1",
            ).success)
            (root / "a.txt").write_text("EXTERNAL", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                tracker.revert_last_changeset("session-a", "ws")
            self.assertEqual((root / "a.txt").read_text(), "EXTERNAL")
            db.close()

    def test_undo_is_session_scoped_and_survives_tracker_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("A0", encoding="utf-8")
            (root / "b.txt").write_text("B0", encoding="utf-8")
            db = HistoryDatabase(in_memory=True)
            tracker = ChangeSetTracker(tmp, db=db, workspace_id="ws")
            applier = DiffApplier(tracker)
            self.assertTrue(applier.apply(
                [EditBlock("a.txt", "A0", "A1")], tmp,
                workspace_id="ws", conversation_id="session-a", turn_id="ta",
            ).success)
            self.assertTrue(applier.apply(
                [EditBlock("b.txt", "B0", "B1")], tmp,
                workspace_id="ws", conversation_id="session-b", turn_id="tb",
            ).success)

            # Session A must undo A even though B is globally newer.
            tracker.revert_last_changeset("session-a", "ws")
            self.assertEqual((root / "a.txt").read_text(), "A0")
            self.assertEqual((root / "b.txt").read_text(), "B1")

            # New tracker simulates daemon restart and loads B from SQLite.
            restarted = ChangeSetTracker(tmp, db=db, workspace_id="ws")
            restarted.revert_last_changeset("session-b", "ws")
            self.assertEqual((root / "b.txt").read_text(), "B0")
            db.close()

    def test_interrupted_undo_restores_post_edit_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("A0", encoding="utf-8")
            (root / "b.txt").write_text("B0", encoding="utf-8")
            db = HistoryDatabase(in_memory=True)
            tracker = ChangeSetTracker(tmp, db=db, workspace_id="ws")
            applier = DiffApplier(tracker)
            self.assertTrue(applier.apply(
                [EditBlock("a.txt", "A0", "A1"), EditBlock("b.txt", "B0", "B1")],
                tmp, workspace_id="ws", conversation_id="session-a", turn_id="t1",
            ).success)
            original = WorkspaceFileSystem.atomic_write

            def fail_second_restore(self, rel, content, **kwargs):
                if str(rel) == "a.txt" and content == "A0":
                    raise OSError("synthetic undo failure")
                return original(self, rel, content, **kwargs)

            with patch.object(WorkspaceFileSystem, "atomic_write", fail_second_restore):
                with self.assertRaises(RuntimeError):
                    tracker.revert_last_changeset("session-a", "ws")
            self.assertEqual((root / "a.txt").read_text(), "A1")
            self.assertEqual((root / "b.txt").read_text(), "B1")
            db.close()

    def test_daemon_replay_cursor_is_per_session(self):
        bridge = DaemonUIBridge(".")
        bridge.attached_session_id = "a"
        bridge._on_wire_event(DaemonEvent(100, "a", "TurnStarted", {}, 1.0))
        bridge.attached_session_id = "b"
        self.assertEqual(bridge.last_sequence_id, 0)
        bridge._on_wire_event(DaemonEvent(7, "b", "TurnStarted", {}, 1.0))
        self.assertEqual(bridge.last_sequence_id, 7)
        bridge.attached_session_id = "a"
        self.assertEqual(bridge.last_sequence_id, 100)

    def test_deep_monorepo_manifest_is_discovered_beyond_depth_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "products" / "finance" / "backend" / "services" / "payments" / "pom.xml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("<project/>", encoding="utf-8")
            scanner = RepositoryScanner(tmp)
            modules = scanner.detect_modules()
            paths = {m["manifest_path"] for m in modules}
            normalized = {str(p).replace("\\", "/") if p else p for p in paths}
            self.assertIn("products/finance/backend/services/payments/pom.xml", normalized)
            limited = {m["manifest_path"] for m in scanner.detect_modules(max_depth=4)}
            limited = {str(p).replace("\\", "/") if p else p for p in limited}
            self.assertNotIn("products/finance/backend/services/payments/pom.xml", limited)

    def test_migration_16_contains_persistent_post_edit_content(self):
        db = HistoryDatabase(in_memory=True)
        with db.get_connection() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(edit_change_snapshots)")}
        self.assertIn("post_content", cols)
        db.close()

    def test_workspace_remembered_write_does_not_enable_apply_patch(self):
        approval_manager = ApprovalManager()
        policy = PolicyEngine(approval_manager=approval_manager)
        self.assertEqual(policy.autonomy.level, "supervised")
        policy.approval_manager.remember("write_file", "**", "allow", "workspace")
        self.assertEqual(policy.evaluate_tool("write_file", {"path": "a.txt"}), "ALLOW")
        self.assertEqual(policy.evaluate_tool("apply_patch", {"patch": "diff"}), "ASK")
        self.assertEqual(policy.autonomy.level, "supervised")

    def test_clear_remembered_does_not_mutate_autonomy(self):
        approval_manager = ApprovalManager()
        policy = PolicyEngine(approval_manager=approval_manager)
        policy.approval_manager.remember("write_file", "**", "allow", "workspace")
        policy.approval_manager.clear_remembered(scope="workspace")
        self.assertEqual(policy.autonomy.level, "supervised")
        self.assertEqual(policy.evaluate_tool("write_file", {"path": "a.txt"}), "ASK")

    def test_session_remember_does_not_mutate_autonomy(self):
        approval_manager = ApprovalManager()
        policy = PolicyEngine(approval_manager=approval_manager)
        policy.approval_manager.remember("write_file", "**", "allow", "session", conversation_id="s1")
        self.assertEqual(policy.autonomy.level, "supervised")
        self.assertEqual(policy.evaluate_tool("write_file", {"path": "a.txt"}, conversation_id="s1"), "ALLOW")
        self.assertEqual(policy.evaluate_tool("write_file", {"path": "a.txt"}, conversation_id="s2"), "ASK")

    def test_redacts_secret_fields_inside_serialized_json(self):
        json_str = '{"api_key": "sk-1234567890abcdef", "name": "test"}'
        clean = sanitize_public_event_payload("ToolCompleted", {"output": json_str})
        self.assertNotIn("sk-1234567890abcdef", str(clean))
        self.assertIn("[REDACTED", str(clean))

    def test_redacts_nested_serialized_json(self):
        nested = '{"payload": "{\\"refresh_token\\": \\"very-secret-token-value\\"}"}'
        clean = sanitize_public_event_payload("ToolCompleted", {"data": nested})
        self.assertNotIn("very-secret-token-value", str(clean))

    def test_redacts_query_string_credentials(self):
        url = "https://example.com/api?api_key=secret-key-123&action=query"
        clean = sanitize_public_event_payload("ToolCompleted", {"url": url})
        self.assertNotIn("secret-key-123", str(clean))

    def test_redacts_yaml_like_credentials(self):
        yaml_text = "api_key: secret-api-value-456\nenv: production"
        clean = sanitize_public_event_payload("ToolCompleted", {"yaml": yaml_text})
        self.assertNotIn("secret-api-value-456", str(clean))
        self.assertIn("env: production", str(clean))

    def test_token_telemetry_is_not_redacted(self):
        payload = {"tokens": 120, "input_tokens": 80, "output_tokens": 40, "max_tokens": 4096}
        clean = sanitize_public_event_payload("ToolCompleted", payload)
        self.assertEqual(clean["tokens"], 120)
        self.assertEqual(clean["input_tokens"], 80)
        self.assertEqual(clean["output_tokens"], 40)
        self.assertEqual(clean["max_tokens"], 4096)

    def test_redaction_is_bounded_against_large_hostile_strings(self):
        large = "A" * (128 * 1024)
        clean = sanitize_public_event_payload("ToolCompleted", {"huge": large})
        self.assertTrue(len(clean["huge"]) <= 65 * 1024)
        self.assertIn("[truncated]", clean["huge"])

    def test_undo_retention_prunes_old_and_excess_changesets(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = HistoryDatabase(in_memory=True)
            tracker = ChangeSetTracker(tmp, db=db, workspace_id="ws")
            tracker.max_changesets_per_session = 5
            for i in range(10):
                fs_snap = FileSnapshot(f"f{i}.txt", False, None)
                tracker.record_changeset(
                    f"edit {i}", [fs_snap],
                    workspace_id="ws", conversation_id="s1", turn_id=f"t{i}",
                    post_hashes={f"f{i}.txt": "h"},
                    post_exists={f"f{i}.txt": True},
                    post_contents={f"f{i}.txt": f"c{i}"},
                )
            with db.get_connection() as conn:
                count = conn.execute("SELECT COUNT(*) FROM edit_changesets WHERE workspace_id='ws' AND conversation_id='s1'").fetchone()[0]
            self.assertLessEqual(count, 5)
            db.close()


if __name__ == "__main__":
    unittest.main()
