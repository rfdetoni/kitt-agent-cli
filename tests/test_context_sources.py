import tempfile
import unittest
from types import SimpleNamespace

from kitt.history.context_builder import HistoryContextBuilder
from kitt.memory.memory_manager import MemoryManager


class TestContextSources(unittest.TestCase):
    def test_memory_context_uses_relevant_items_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryManager(tmpdir, persistence_enabled=True)
            memory.add_project_memory("Prefer pytest for payment service tests.")
            memory.add_project_memory("Unrelated deployment note.")

            context = memory.get_memory_context("payment tests", max_tokens=80)

            self.assertIn("payment service tests", context)
            self.assertNotIn("Unrelated deployment note", context)

    def test_history_builder_skips_oversized_items_and_keeps_older_fit(self):
        entries = [
            SimpleNamespace(include_in_context=True, payload={"role": "user", "content": "small old"}, entry_type="MESSAGE"),
            SimpleNamespace(include_in_context=True, payload={"role": "assistant", "content": "x" * 8000}, entry_type="MESSAGE"),
            SimpleNamespace(include_in_context=True, payload={"role": "user", "content": "small new"}, entry_type="MESSAGE"),
        ]
        tree = SimpleNamespace(get_active_path=lambda _conversation_id: entries)

        context = HistoryContextBuilder(tree).build("conv", max_tokens=50)

        self.assertIn("small old", context)
        self.assertIn("small new", context)
        self.assertNotIn("x" * 100, context)


if __name__ == "__main__":
    unittest.main()
