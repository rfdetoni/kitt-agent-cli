import unittest
from unittest.mock import MagicMock

from kitt.memory.memory_manager import MemoryManager


class TestMemoryManagerShared(unittest.TestCase):
    def test_kitt_memory_is_the_only_write_authority(self):
        client = MagicMock()
        manager = MemoryManager(shared_client=client, workspace_id="ws1")
        manager.add_project_memory("Use pytest", kind="PROJECT_RULE", pinned=True)
        client.remember.assert_called_once_with("ws1", "Use pytest", kind="PROJECT_RULE", pinned=True)

    def test_recall_comes_only_from_kitt_memory(self):
        client = MagicMock()
        client.recall.return_value = [{
            "id": "mem-1",
            "workspace_id": "ws1",
            "scope": "workspace",
            "content": "Architecture decision: services",
            "pinned": True,
        }]
        manager = MemoryManager(shared_client=client, workspace_id="ws1")
        relevant = manager.get_relevant_memories("services")
        self.assertEqual([item.text for item in relevant], ["Architecture decision: services"])

    def test_progressive_memory_hydrates_then_keeps_budgeted_snippet(self):
        class ProgressiveClient:
            def search(self, workspace_id, query, **kwargs):
                self.search_args = (workspace_id, query, kwargs)
                return (
                    [
                        {
                            "id": "mem-1",
                            "snippet": "short one",
                            "scope": "workspace",
                        },
                        {
                            "id": "mem-2",
                            "snippet": "snippet retained under pressure",
                            "scope": "workspace",
                        },
                    ],
                    "trace-search",
                )

            def get(self, workspace_id, ids, **kwargs):
                self.get_args = (workspace_id, list(ids), kwargs)
                return (
                    [
                        {
                            "record": {
                                "id": "mem-1",
                                "scope": "workspace",
                                "content": "full hydrated memory",
                                "pinned": True,
                            },
                            "provenance": [],
                        }
                    ],
                    "trace-get",
                    ["mem-2"],
                )

        client = ProgressiveClient()
        manager = MemoryManager(shared_client=client, workspace_id="ws1")
        relevant = manager.get_relevant_memories("services", max_tokens=300)

        self.assertEqual(
            [item.text for item in relevant],
            ["full hydrated memory", "snippet retained under pressure"],
        )
        self.assertEqual("trace-get", relevant[0].recall_trace_id)
        self.assertEqual("trace-search", relevant[1].recall_trace_id)
        self.assertEqual(300, client.get_args[2]["token_budget"])
        self.assertEqual(150, client.search_args[2]["token_budget"])

    def test_clear_archives_in_kitt_memory(self):
        client = MagicMock()
        manager = MemoryManager(shared_client=client, workspace_id="ws1")
        manager.clear_project_memory()
        client.manage.assert_called_once_with(
            "archive_workspace",
            {"namespace": "agent-cli", "workspace_id": "ws1"},
        )

    def test_reusable_knowledge_writes_use_kitt_memory_manage(self):
        client = MagicMock()
        client.manage.side_effect = [
            {"correction": {"id": "corr-1"}},
            {"concept": {"id": "concept-1", "name": "KITT"}},
            {"edge": {"id": "edge-1"}},
        ]
        manager = MemoryManager(shared_client=client, workspace_id="ws1")

        self.assertEqual(
            manager.remember_correction("ctx", "old", "new", "reason"),
            "corr-1",
        )
        concept = manager.remember_concept(
            "KITT",
            "shared memory authority",
            labels=["architecture"],
        )
        self.assertEqual(concept["id"], "concept-1")
        self.assertEqual(
            manager.link_concepts("concept-1", "concept-2", "RELATED"),
            "edge-1",
        )

        operations = [call.args[0] for call in client.manage.call_args_list]
        self.assertEqual(
            operations,
            ["correction.record", "concept.upsert", "concept.link"],
        )

    def test_no_markdown_or_local_repository_fallback_exists(self):
        client = MagicMock()
        manager = MemoryManager(shared_client=client, workspace_id="ws1")
        self.assertFalse(hasattr(manager, "global_mem_path"))
        self.assertFalse(hasattr(manager, "project_mem_path"))


if __name__ == "__main__":
    unittest.main()
