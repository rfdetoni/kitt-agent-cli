import unittest
import tempfile
import shlex
import subprocess
from pathlib import Path
from kitt.domain.entities import EditBlock
from kitt.edit_format.applier import DiffApplier
from kitt.edit_format.changeset import ChangeSetTracker

class TestPhase0SecurityAndContainment(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.tracker = ChangeSetTracker(root_dir=self.tmp_dir.name)
        self.applier = DiffApplier(changeset_tracker=self.tracker)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_path_traversal_blocked(self):
        # Path traversal with relative ../
        block = EditBlock(
            file_path="../../etc/passwd",
            search_content="",
            replace_content="malicious_entry",
            is_new_file=True
        )
        res = self.applier.apply([block], root_dir=self.tmp_dir.name)
        self.assertFalse(res.success)
        self.assertTrue(any("Path containment violation" in err for err in res.errors))

    def test_forbidden_files_blocked(self):
        # Protected file .env or .git
        block_env = EditBlock(
            file_path=".env",
            search_content="",
            replace_content="API_KEY=stolen",
            is_new_file=True
        )
        res = self.applier.apply([block_env], root_dir=self.tmp_dir.name)
        self.assertFalse(res.success)
        self.assertTrue(any("Access denied" in err for err in res.errors))

    def test_changeset_undo_preserves_user_uncommitted_changes(self):
        # 1. Pre-existing user uncommitted modification
        user_file = self.root_path / "user_work.py"
        user_file.write_text("def user_feature(): pass\n", encoding='utf-8')

        # 2. K.I.T.T. applies edit to kitt_module.py
        kitt_file = self.root_path / "kitt_module.py"
        kitt_file.write_text("def old_function(): pass\n", encoding='utf-8')

        edit_block = EditBlock(
            file_path="kitt_module.py",
            search_content="def old_function(): pass",
            replace_content="def new_function(): pass"
        )
        res = self.applier.apply([edit_block], root_dir=self.tmp_dir.name)
        self.assertTrue(res.success)
        self.assertEqual(kitt_file.read_text(), "def new_function(): pass\n")

        # User modifies user_work.py again
        user_file.write_text("def user_feature(): return 42\n", encoding='utf-8')

        # 3. K.I.T.T. performs ChangeSet undo
        reverted_cs = self.tracker.revert_last_changeset()
        self.assertIsNotNone(reverted_cs)

        # Verify kitt_module.py is reverted to old_function
        self.assertEqual(kitt_file.read_text(), "def old_function(): pass\n")

        # Verify user_work.py remains INTACT with user changes!
        self.assertEqual(user_file.read_text(), "def user_feature(): return 42\n")

    def test_safe_command_parsing(self):
        cmd = "echo 'hello world'"
        args = shlex.split(cmd)
        self.assertEqual(args, ["echo", "hello world"])
        res = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(res.stdout.strip(), "hello world")

if __name__ == '__main__':
    unittest.main()
