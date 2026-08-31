import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from kitt.memory.memory_manager import MemoryManager, MemoryItem
from kitt.memory.shared_client import SharedMemoryUnavailable

class TestMemoryManagerShared(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_standalone_when_shared_client_none(self):
        mgr = MemoryManager(root_dir=str(self.root), shared_client=None, workspace_id="ws1")
        mgr.add_project_memory("Use pytest", kind="PROJECT_RULE", pinned=True)
        items = mgr.get_items()
        self.assertTrue(any("Use pytest" in item.text for item in items))

    def test_dual_write_and_shared_unavailable_graceful(self):
        mock_client = MagicMock()
        mock_client.remember.side_effect = SharedMemoryUnavailable("Connection refused")
        mock_client.recall.side_effect = SharedMemoryUnavailable("Connection refused")

        mgr = MemoryManager(root_dir=str(self.root), shared_client=mock_client, workspace_id="ws1")
        # Should not raise exception
        mgr.add_project_memory("Write clean code", kind="PROJECT_RULE", pinned=True)
        mock_client.remember.assert_called_once_with("ws1", "Write clean code", kind="PROJECT_RULE", pinned=True)

        # Recall should fall back gracefully without failing
        relevant = mgr.get_relevant_memories("clean code")
        self.assertTrue(any("Write clean code" in item.text for item in relevant))

    def test_shared_recall_augments_items(self):
        mock_client = MagicMock()
        mock_client.recall.return_value = [
            {"content": "Architecture decision: microservices", "pinned": True}
        ]

        mgr = MemoryManager(root_dir=str(self.root), shared_client=mock_client, workspace_id="ws1")
        relevant = mgr.get_relevant_memories("architecture microservices")
        self.assertTrue(any("microservices" in item.text for item in relevant))

if __name__ == "__main__":
    unittest.main()
