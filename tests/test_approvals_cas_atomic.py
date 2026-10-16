import time
import unittest
from kitt.history.database import HistoryDatabase
from kitt.tools.approval import ApprovalManager

class TestApprovalsCASAtomic(unittest.TestCase):
    def setUp(self):
        self.db = HistoryDatabase(in_memory=True)
        self.mgr = ApprovalManager(db=self.db)

    def tearDown(self):
        self.db.close()

    def test_grant_without_pending_request_is_rejected(self):
        grant = self.mgr.issue_grant("t1", "c1", "w1", "hash_action", approval_id="app_1")
        self.assertIsNone(grant)

    def test_valid_request_grant_consume_flow(self):
        req = self.mgr.register_request("t1", "c1", "w1", "hash1", "app_1", "write_file", "Write test")
        self.assertEqual(req.state, "PENDING")

        grant = self.mgr.issue_grant("t1", "c1", "w1", "hash1", "app_1")
        self.assertIsNotNone(grant)

        # Single consume succeeds
        success = self.mgr.validate_and_consume(grant, "hash1", "t1", "c1", "w1", "app_1")
        self.assertTrue(success)

        # Replay attempt fails!
        replay = self.mgr.validate_and_consume(grant, "hash1", "t1", "c1", "w1", "app_1")
        self.assertFalse(replay)

    def test_deny_does_not_mutate_memory_when_durable_write_fails(self):
        manager = ApprovalManager()
        manager.register_request(
            "t-deny", "c-deny", "w-deny", "hash-deny", "app-deny", "write_file"
        )

        class FailingDb:
            def get_connection(self):
                raise RuntimeError("database unavailable")

        manager.db = FailingDb()
        self.assertFalse(manager.deny("app-deny"))
        self.assertEqual(
            [item.approval_id for item in manager.list_pending("w-deny")],
            ["app-deny"],
        )

    def test_mismatched_bindings_rejected(self):
        self.mgr.register_request("t1", "c1", "w1", "hash1", "app_1", "write_file", "Write test")
        grant = self.mgr.issue_grant("t1", "c1", "w1", "hash1", "app_1")

        # Wrong turn_id
        self.assertFalse(self.mgr.validate_and_consume(grant, "hash1", "WRONG_TURN", "c1", "w1", "app_1"))
        # Wrong workspace_id
        self.assertFalse(self.mgr.validate_and_consume(grant, "hash1", "t1", "c1", "WRONG_WS", "app_1"))
        # Wrong action hash
        self.assertFalse(self.mgr.validate_and_consume(grant, "WRONG_HASH", "t1", "c1", "w1", "app_1"))


    def test_repeated_pending_request_is_idempotent(self):
        original = self.mgr.register_request(
            "t1", "c1", "w1", "hash1", "app_1", "write_file"
        )
        repeated = self.mgr.register_request(
            "t1", "c1", "w1", "hash1", "app_1", "write_file"
        )
        self.assertEqual(original, repeated)
        self.assertEqual(len(self.mgr.list_pending("w1")), 1)

    def test_decided_request_cannot_be_reopened(self):
        self.mgr.register_request(
            "t1", "c1", "w1", "hash1", "app_1", "write_file"
        )
        grant = self.mgr.issue_grant("t1", "c1", "w1", "hash1", "app_1")
        self.assertIsNotNone(grant)
        with self.assertRaisesRegex(ValueError, "already been decided"):
            self.mgr.register_request(
                "t1", "c1", "w1", "hash1", "app_1", "write_file"
            )
        with self.db.get_connection() as conn:
            state = conn.execute(
                "SELECT state FROM approval_requests WHERE approval_id=?",
                ("app_1",),
            ).fetchone()["state"]
        self.assertEqual(state, "GRANTED")
        self.assertTrue(self.mgr.validate_and_consume(
            grant, "hash1", "t1", "c1", "w1", "app_1"
        ))

    def test_conflicting_action_cannot_reuse_approval_id(self):
        self.mgr.register_request(
            "t1", "c1", "w1", "hash1", "app_1", "write_file"
        )
        with self.assertRaisesRegex(ValueError, "different action"):
            self.mgr.register_request(
                "t1", "c1", "w1", "hash2", "app_1", "write_file"
            )
        self.assertEqual(len(self.mgr.list_pending("w1")), 1)

    def test_failed_registration_does_not_publish_phantom_request(self):
        manager = ApprovalManager()

        class FailingDb:
            def get_connection(self):
                raise RuntimeError("database unavailable")

        manager.db = FailingDb()
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            manager.register_request(
                "t1", "c1", "w1", "hash1", "app_1", "write_file"
            )
        self.assertEqual(manager.list_pending("w1"), [])

    def test_decided_request_stays_closed_across_manager_restart(self):
        self.mgr.register_request(
            "t1", "c1", "w1", "hash1", "app_1", "write_file"
        )
        self.assertTrue(self.mgr.issue_grant(
            "t1", "c1", "w1", "hash1", "app_1"
        ))
        restarted = ApprovalManager(db=self.db)
        with self.assertRaisesRegex(ValueError, "already been decided"):
            restarted.register_request(
                "t1", "c1", "w1", "hash1", "app_1", "write_file"
            )

if __name__ == "__main__":
    unittest.main()
