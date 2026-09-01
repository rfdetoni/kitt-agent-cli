import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from kitt.memory.memory_manager import MemoryManager
from kitt.memory.shared_client import SharedMemoryUnavailable


class TestMemoryManagerShared(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_shared_backend_is_authoritative_when_available(self):
        shared = MagicMock()
        manager = MemoryManager(
            root_dir=str(self.root),
            shared_client=shared,
            workspace_id="ws1",
        )
        manager.add_project_memory("Use pytest", kind="PROJECT_RULE", pinned=True)

        shared.remember.assert_called_once_with(
            "ws1", "Use pytest", kind="PROJECT_RULE", pinned=True
        )
        self.assertNotIn("- Use pytest", manager.project_mem_path.read_text())

    def test_unavailable_shared_falls_back_to_markdown(self):
        shared = MagicMock()
        shared.remember.side_effect = SharedMemoryUnavailable("Connection refused")
        shared.recall.side_effect = SharedMemoryUnavailable("Connection refused")

        manager = MemoryManager(
            root_dir=str(self.root),
            shared_client=shared,
            workspace_id="ws1",
        )
        manager.add_project_memory("Write clean code", kind="PROJECT_RULE", pinned=True)

        relevant = manager.get_relevant_memories("clean code")
        self.assertTrue(any("Write clean code" in item.text for item in relevant))

    def test_shared_recall_does_not_mix_stale_local_records(self):
        shared = MagicMock()
        shared.recall.return_value = [
            {"content": "Architecture decision: services", "pinned": True}
        ]
        manager = MemoryManager(
            root_dir=str(self.root),
            shared_client=shared,
            workspace_id="ws1",
        )
        manager._append_markdown("stale local value")

        relevant = manager.get_relevant_memories("architecture services")
        self.assertTrue(any("services" in item.text for item in relevant))
        self.assertFalse(any("stale local" in item.text for item in relevant))


if __name__ == "__main__":
    unittest.main()
