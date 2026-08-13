import tempfile
import unittest
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.approval import ApprovalManager
from kitt.core.autonomy_store import AutonomyStore

class TestAlwaysAllowPermissions(unittest.TestCase):
    def test_remembered_approval_rule_allows_file_write(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            approval = ApprovalManager()
            policy = PolicyEngine(root_dir=tmp_dir, approval_manager=approval)

            # 1. Initially ask for approval
            self.assertEqual(policy.evaluate_tool("apply_patch", {"patch": "some diff"}), "ASK")
            self.assertEqual(policy.evaluate_tool("write_file", {"path": "page.html"}), "ASK")

            # 2. Remember rule to always allow apply_patch and write_file
            approval.remember("apply_patch", "**", "allow", "workspace")
            approval.remember("write_file", "**", "allow", "workspace")

            # 3. Subsequent calls return ALLOW
            self.assertEqual(policy.evaluate_tool("apply_patch", {"patch": "some diff"}), "ALLOW")
            self.assertEqual(policy.evaluate_tool("write_file", {"path": "page.html"}), "ALLOW")

    def test_autonomy_preset_files_free_allows_file_writes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AutonomyStore(root_dir=tmp_dir, persistence_enabled=False)
            policy = PolicyEngine(root_dir=tmp_dir, autonomy=store.get())

            # 1. Supervised requires approval
            self.assertEqual(policy.evaluate_tool("apply_patch", {"patch": "diff"}), "ASK")

            # 2. Set preset files_free (Always Allow Files)
            new_policy = store.set_preset("files_free")
            policy.autonomy = new_policy

            # 3. Returns ALLOW automatically
            self.assertEqual(policy.evaluate_tool("apply_patch", {"patch": "diff"}), "ALLOW")
            self.assertEqual(policy.evaluate_tool("write_file", {"path": "file.py"}), "ALLOW")

if __name__ == "__main__":
    unittest.main()
