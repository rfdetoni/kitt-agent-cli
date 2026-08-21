import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

from kitt.core.pending_action import PendingAction
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import ApprovalRequired, TurnFailed
from kitt.core.turn_processor import TurnProcessor
from kitt.history.database import HistoryDatabase
from kitt.security.mutation_preconditions import (
    MutationPrecondition,
    capture_preconditions,
    validate_preconditions,
)
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.registry import ToolRegistry


class AntigravityHardeningTests(unittest.TestCase):
    def test_precondition_capture_and_validation_write_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "existing.txt").write_text("v1", encoding="utf-8")
            
            precs = capture_preconditions(tmp, "write_file", {"path": "existing.txt", "content": "v2"})
            self.assertEqual(len(precs), 1)
            self.assertTrue(precs[0].expected_exists)
            self.assertEqual(precs[0].expected_sha256, hashlib.sha256(b"v1").hexdigest())

            valid, err = validate_preconditions(tmp, precs)
            self.assertTrue(valid)

            # Modify externally
            (root / "existing.txt").write_text("v1_modified", encoding="utf-8")
            valid, err = validate_preconditions(tmp, precs)
            self.assertFalse(valid)
            self.assertIn("was modified after approval request", err)

    def test_precondition_capture_and_validation_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            precs = capture_preconditions(tmp, "write_file", {"path": "new.txt", "content": "hello"})
            self.assertEqual(len(precs), 1)
            self.assertFalse(precs[0].expected_exists)

            valid, err = validate_preconditions(tmp, precs)
            self.assertTrue(valid)

            # Created externally
            (root / "new.txt").write_text("surprise", encoding="utf-8")
            valid, err = validate_preconditions(tmp, precs)
            self.assertFalse(valid)
            self.assertIn("was created after approval request", err)

    def test_precondition_capture_and_validation_apply_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("foo", encoding="utf-8")
            patch = "<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE"
            precs = capture_preconditions(tmp, "apply_patch", {"patch": f"*** a.txt\n{patch}"})
            self.assertEqual(len(precs), 1)
            self.assertTrue(precs[0].expected_exists)

            # Modify externally
            (root / "a.txt").write_text("modified", encoding="utf-8")
            valid, err = validate_preconditions(tmp, precs)
            self.assertFalse(valid)
            self.assertIn("was modified after approval request", err)

    def test_turn_processor_approval_fails_closed_if_file_modified_before_resume(self):
        from kitt.security.context import ExecutionSecurityContext
        from kitt.core.pending_action import canonical_args_digest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.txt").write_text("original", encoding="utf-8")
            db = HistoryDatabase(in_memory=True)
            registry = ToolRegistry(tmp)
            registry.attach_services(db=db)
            processor = TurnProcessor(tmp, registry=registry)
            processor.history_service = type("HS", (), {"repo": db})()

            args = {"path": "target.txt", "content": "updated"}
            action_hash = registry.policy.generate_action_hash("write_file", args)
            approval_id = "req_turn1"
            registry.approval_manager.register_request(
                "turn-1", "s1", "local", action_hash, approval_id, tool_name="write_file"
            )
            grant = registry.approval_manager.issue_grant(
                "turn-1", "s1", "local", action_hash, approval_id=approval_id
            )
            precs = capture_preconditions(tmp, "write_file", args)
            from kitt.security.capabilities import CAP_REPO_WRITE
            sec = ExecutionSecurityContext.create_user_context("local", "s1", "turn-1", capabilities={CAP_REPO_WRITE})
            sec_dict = sec.to_dict()
            sec_dict["mutation_preconditions"] = [p.to_dict() for p in precs]
            pa = PendingAction(
                id="pa_turn-1",
                approval_request_id=approval_id,
                turn_id="turn-1",
                conversation_id="s1",
                workspace_id="local",
                tool_name="write_file",
                normalized_args=args,
                action_hash=action_hash,
                source_response_sha256=canonical_args_digest(args),
                affected_paths=[p.path for p in precs],
                before_hashes={p.path: p.expected_sha256 for p in precs},
                created_at=1.0,
                expires_at=9999999999.0,
                state="pending",
                security_context=sec_dict,
            )
            from kitt.history.repository import HistoryRepository
            repo = HistoryRepository(db)
            processor.history_service = type("HS", (), {"repo": repo})()
            with db.get_connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO workspaces (id, canonical_path_hash, display_name, created_at, last_opened_at) VALUES ('local', 'hash_local', 'local', 1.0, 1.0)"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO conversations (id, workspace_id, title, created_at, updated_at) VALUES ('s1', 'local', 'test', 1.0, 1.0)"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO turns (id, conversation_id, ordinal, started_at) VALUES ('turn-1', 's1', 1, 1.0)"
                )
                conn.execute(
                    "INSERT INTO approval_requests (approval_id, conversation_id, turn_id, workspace_id, tool_name, arguments_hash, scope_json, risk_level, state, nonce_hash, requested_at, expires_at) VALUES (?, 's1', 'turn-1', 'local', 'write_file', ?, '{}', 'LOW', 'PENDING', 'nonce', '2026-01-01T00:00:00Z', '2030-01-01T00:00:00Z')",
                    (approval_id, action_hash)
                )
            processor.pending_actions["turn-1"] = pa
            repo.save_pending_action(pa)

            # External modification before grant continuation
            (root / "target.txt").write_text("external_edit", encoding="utf-8")

            resume_events = list(processor.continue_turn("turn-1", grant))
            self.assertTrue(any(isinstance(e, TurnFailed) for e in resume_events))
            self.assertIn("was modified after approval request", resume_events[0].error)
            # Verify file stayed intact with external content
            self.assertEqual((root / "target.txt").read_text(), "external_edit")
            db.close()


if __name__ == "__main__":
    unittest.main()
