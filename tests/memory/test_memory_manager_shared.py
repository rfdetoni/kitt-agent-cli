import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from kitt.memory.memory_manager import MemoryManager
from kitt.memory.shared_client import SharedMemoryUnavailable


class TestMemoryManagerShared(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_agent_repository_is_canonical_and_shared_is_mirrored(self):
        shared = MagicMock()
        repo = MagicMock()
        manager = MemoryManager(
            root_dir=str(self.root),
            shared_client=shared,
            memory_repo=repo,
            workspace_id="ws1",
        )

        manager.add_project_memory("Use pytest", kind="PROJECT_RULE", pinned=True)

        repo.add_direct_memory.assert_called_once_with(
            "ws1", "Use pytest", kind="PROJECT_RULE", pinned=True
        )
        shared.remember.assert_called_once_with(
            "ws1", "Use pytest", kind="PROJECT_RULE", pinned=True
        )
        self.assertNotIn("- Use pytest", manager.project_mem_path.read_text())

    def test_shared_outage_never_hides_local_structured_memory(self):
        shared = MagicMock()
        shared.remember.side_effect = SharedMemoryUnavailable("Connection refused")
        shared.recall.side_effect = SharedMemoryUnavailable("Connection refused")
        repo = MagicMock()
        repo.get_active_memories.return_value = [
            SimpleNamespace(
                content="Write clean code",
                pinned=True,
                kind="PROJECT_RULE",
            )
        ]

        manager = MemoryManager(
            root_dir=str(self.root),
            shared_client=shared,
            memory_repo=repo,
            workspace_id="ws1",
        )
        manager.add_project_memory("Write clean code", kind="PROJECT_RULE", pinned=True)

        relevant = manager.get_relevant_memories("clean code")
        self.assertTrue(any("Write clean code" in item.text for item in relevant))
        repo.add_direct_memory.assert_called_once()

    def test_shared_recall_is_merged_with_local_records_instead_of_replacing_them(self):
        shared = MagicMock()
        shared.recall.return_value = [
            {
                "id": "shared-1",
                "workspace_id": "ws1",
                "scope": "workspace",
                "content": "Architecture decision: services",
                "pinned": True,
            }
        ]
        repo = MagicMock()
        repo.get_active_memories.return_value = [
            SimpleNamespace(
                content="Local durable rule",
                pinned=True,
                kind="PROJECT_RULE",
            )
        ]
        manager = MemoryManager(
            root_dir=str(self.root),
            shared_client=shared,
            memory_repo=repo,
            workspace_id="ws1",
        )

        relevant = manager.get_relevant_memories("architecture local services")
        self.assertTrue(any("services" in item.text for item in relevant))
        self.assertTrue(any("Local durable rule" in item.text for item in relevant))

    def test_markdown_is_only_the_last_resort_and_is_cross_process_safe(self):
        shared = MagicMock()
        shared.remember.side_effect = SharedMemoryUnavailable("Connection refused")
        shared.recall.side_effect = SharedMemoryUnavailable("Connection refused")
        manager = MemoryManager(
            root_dir=str(self.root),
            shared_client=shared,
            workspace_id="ws1",
        )

        manager.add_project_memory("Fallback note")
        manager.add_project_memory("Fallback note")

        content = manager.project_mem_path.read_text()
        self.assertEqual(content.count("- Fallback note"), 1)

    def test_clear_archives_local_records_and_deletes_exact_shared_mirrors(self):
        shared = MagicMock()
        repo = MagicMock()
        archived = SimpleNamespace(content="Use pytest")
        repo.archive_active_memories.return_value = [archived]
        shared.recall.return_value = [
            {
                "id": "shared-1",
                "workspace_id": "ws1",
                "scope": "workspace",
                "content": "Use pytest",
                "pinned": True,
            }
        ]
        shared.forget.return_value = True
        manager = MemoryManager(
            root_dir=str(self.root),
            shared_client=shared,
            memory_repo=repo,
            workspace_id="ws1",
        )

        manager.clear_project_memory()

        repo.archive_active_memories.assert_called_once_with("ws1")
        shared.forget.assert_called_once_with("shared-1")
        self.assertEqual(
            manager.project_mem_path.read_text(),
            "# Project Memory & Guidelines\n\n",
        )

    def test_default_memory_files_do_not_invent_user_preferences(self):
        manager = MemoryManager(root_dir=str(self.root), shared_client=MagicMock())
        self.assertNotIn("Prefer standard library", manager.global_mem_path.read_text())
        self.assertNotIn("Write clean", manager.project_mem_path.read_text())


if __name__ == "__main__":
    unittest.main()
