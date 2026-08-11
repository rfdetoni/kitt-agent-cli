import unittest
import tempfile
from pathlib import Path
from kitt.history.service import HistoryService

class TestPhase2History(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.history_service = HistoryService(root_dir=self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_create_list_and_resume_conversation(self):
        conv1 = self.history_service.new_conversation(title="First Task")
        self.assertIsNotNone(conv1["id"])

        self.history_service.repo.save_message(conv1["id"], "t1", "user", "Hello K.I.T.T.")
        self.history_service.repo.save_message(conv1["id"], "t1", "assistant", "Hello User.")

        history_list = self.history_service.list_history()
        self.assertEqual(len(history_list), 1)
        self.assertEqual(history_list[0]["title"], "First Task")

        resumed = self.history_service.resume_conversation("1")
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed["id"], conv1["id"])

    def test_fork_conversation(self):
        conv1 = self.history_service.new_conversation(title="Original")
        self.history_service.repo.save_message(conv1["id"], "t1", "user", "Message 1")

        forked = self.history_service.fork_conversation()
        self.assertNotEqual(forked["id"], conv1["id"])
        self.assertIn("Original (Fork)", forked["title"])

        msgs = self.history_service.repo.get_messages_for_conversation(forked["id"])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"], "Message 1")

if __name__ == '__main__':
    unittest.main()
