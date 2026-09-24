import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from kitt.tools.approval import ApprovalManager
from kitt.history.database import HistoryDatabase
from kitt.ui.state import UIState
from kitt.ui.components.permission_card import PermissionCardComponent

class TestApprovalCenter(unittest.TestCase):
    def test_queue_counter_and_rendering(self):
        manager = ApprovalManager()
        req1 = manager.register_request("t1", "c1", "ws1", "hash1", "req1", "write_file", "summary 1")
        req2 = manager.register_request("t1", "c1", "ws1", "hash2", "req2", "write_file", "summary 2")
        req3 = manager.register_request("t1", "c1", "ws1", "hash3", "req3", "write_file", "summary 3")

        pending_list = manager.list_pending("ws1")
        self.assertEqual(len(pending_list), 3)

        state = UIState()
        state.pending_approvals = [
            {"approval_id": "req1", "tool_name": "write_file", "args": {"file": "src/app.py"}},
            {"approval_id": "req2", "tool_name": "write_file", "args": {"file": "src/utils.py"}},
            {"approval_id": "req3", "tool_name": "write_file", "args": {"file": "src/index.py"}},
        ]

        card_output = PermissionCardComponent().render(state, width=88)
        self.assertIn("(1 de 3 na fila)", card_output)
        self.assertIn("Menu de aprovação", card_output)
        self.assertIn("> [y] Permitir uma vez", card_output)
        self.assertIn("sem timeout", card_output)
        self.assertNotIn("expira em", card_output)

    def test_pending_request_survives_arbitrary_wait_until_user_decides(self):
        manager = ApprovalManager(ttl_seconds=5)
        req = manager.register_request(
            "turn-long-wait", "conv-long-wait", "ws1", "hash-long-wait",
            "req-long-wait", "process.run", "Long-lived approval"
        )
        self.assertEqual(req.expires_at, 0.0)

        future = req.created_at + 24 * 60 * 60
        with patch("kitt.tools.approval.time.time", return_value=future):
            self.assertEqual(manager.expire_pending(), 0)
            self.assertEqual(
                [item.approval_id for item in manager.list_pending("ws1")],
                ["req-long-wait"],
            )
            grant = manager.issue_grant(
                "turn-long-wait", "conv-long-wait", "ws1",
                "hash-long-wait", "req-long-wait"
            )

        self.assertIsNotNone(grant)
        self.assertGreater(grant.expires_at, grant.granted_at)

    def test_persisted_pending_request_survives_restart_and_long_wait(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            db_path = Path(tmp_dir) / "kitt.db"
            db = HistoryDatabase(str(db_path))
            manager = ApprovalManager(ttl_seconds=5, db=db)
            req = manager.register_request(
                "turn-restart",
                "conv-restart",
                "ws-restart",
                "hash-restart",
                "req-restart",
                "process.run",
                "Persistent approval",
            )
            self.assertEqual(req.expires_at, 0.0)

            # Simulate a daemon/runtime restart: the new broker has no in-memory
            # request state and must rehydrate the durable PENDING row.
            restarted = ApprovalManager(ttl_seconds=5, db=db)
            future = req.created_at + 14 * 24 * 60 * 60
            with patch("kitt.tools.approval.time.time", return_value=future):
                grant = restarted.issue_grant(
                    "turn-restart",
                    "conv-restart",
                    "ws-restart",
                    "hash-restart",
                    "req-restart",
                )

            self.assertIsNotNone(grant)
            self.assertGreater(grant.expires_at, grant.granted_at)

    def test_remembered_approval_rules_and_persistence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            db_path = Path(tmp_dir) / "kitt.db"
            db = HistoryDatabase(str(db_path))

            manager = ApprovalManager(db=db)
            manager.remember("write_file", "src/**/*.py", "allow", "workspace")

            # Check remembered rule match
            self.assertEqual(manager.check_remembered("write_file", "src/app.py"), "allow")
            self.assertIsNone(manager.check_remembered("write_file", "tests/x.py"))

            # Check persistence across new ApprovalManager instance
            manager2 = ApprovalManager(db=db)
            self.assertEqual(manager2.check_remembered("write_file", "src/app.py"), "allow")
            self.assertIsNone(manager2.check_remembered("write_file", "tests/x.py"))

    def test_single_use_nonce_prevention(self):
        manager = ApprovalManager()
        req = manager.register_request("t1", "c1", "ws1", "hash1", "req1", "write_file")
        grant = manager.issue_grant("t1", "c1", "ws1", "hash1", "req1")

        # First consumption -> True
        consumed1 = manager.validate_and_consume(grant, "hash1", "t1", "c1", "ws1", "req1")
        self.assertTrue(consumed1)

        # Second consumption (same nonce) -> False
        consumed2 = manager.validate_and_consume(grant, "hash1", "t1", "c1", "ws1", "req1")
        self.assertFalse(consumed2)

if __name__ == "__main__":
    unittest.main()
