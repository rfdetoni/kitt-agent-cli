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

    def test_mismatched_bindings_rejected(self):
        self.mgr.register_request("t1", "c1", "w1", "hash1", "app_1", "write_file", "Write test")
        grant = self.mgr.issue_grant("t1", "c1", "w1", "hash1", "app_1")

        # Wrong turn_id
        self.assertFalse(self.mgr.validate_and_consume(grant, "hash1", "WRONG_TURN", "c1", "w1", "app_1"))
        # Wrong workspace_id
        self.assertFalse(self.mgr.validate_and_consume(grant, "hash1", "t1", "c1", "WRONG_WS", "app_1"))
        # Wrong action hash
        self.assertFalse(self.mgr.validate_and_consume(grant, "WRONG_HASH", "t1", "c1", "w1", "app_1"))

if __name__ == "__main__":
    unittest.main()
