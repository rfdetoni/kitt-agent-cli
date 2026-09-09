import unittest

from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.history.search_index import HistorySearchIndex


class HistorySearchIndexTests(unittest.TestCase):
    def setUp(self):
        self.db = HistoryDatabase(":memory:", in_memory=True)
        self.repo = HistoryRepository(self.db)
        self.search = HistorySearchIndex(self.db)
        self.ws_a = self.repo.get_or_create_workspace("workspace-search-a")
        self.ws_b = self.repo.get_or_create_workspace("workspace-search-b")

    def tearDown(self):
        self.db.close()

    def _conversation(self, workspace_id, title, message, turn_suffix):
        conv = self.repo.create_conversation(workspace_id, title=title)
        self.repo.save_message(
            conv["id"],
            f"turn-{turn_suffix}",
            "user",
            message,
        )
        return conv

    def test_search_matches_message_content_without_returning_full_history(self):
        conv = self._conversation(
            self.ws_a["id"],
            "Performance work",
            "Investigate the reverse proxy chromium session recycler and websocket latency. " * 20,
            "a",
        )

        rows = self.search.search(self.ws_a["id"], "chromium recycler")

        self.assertEqual(rows[0]["id"], conv["id"])
        self.assertEqual(rows[0]["match_source"], "message")
        self.assertIn("chromium", rows[0]["match_snippet"].lower())
        self.assertLessEqual(
            len(rows[0]["match_snippet"]),
            HistorySearchIndex.MAX_SNIPPET_CHARS,
        )
        self.assertNotIn("messages", rows[0])
        self.assertIn(rows[0]["search_backend"], {"fts5", "bounded_like"})

    def test_index_syncs_messages_added_after_initial_search(self):
        conv = self._conversation(
            self.ws_a["id"],
            "Incremental",
            "first searchable marker alpha",
            "first",
        )
        self.assertEqual(
            self.search.search(self.ws_a["id"], "alpha")[0]["id"],
            conv["id"],
        )

        self.repo.save_message(
            conv["id"],
            "turn-second",
            "assistant",
            "newly persisted zeta-gateway capability",
        )

        rows = self.search.search(self.ws_a["id"], "zeta gateway")
        self.assertEqual(rows[0]["id"], conv["id"])
        self.assertIn("zeta", rows[0]["match_snippet"].lower())

    def test_workspace_boundary_is_enforced_inside_search_query(self):
        visible = self._conversation(
            self.ws_a["id"],
            "Visible session",
            "workspace boundary sentinel",
            "visible",
        )
        self._conversation(
            self.ws_b["id"],
            "Secret other workspace",
            "workspace boundary sentinel",
            "other",
        )

        rows = self.search.search(self.ws_a["id"], "boundary sentinel")
        self.assertEqual([row["id"] for row in rows], [visible["id"]])
        self.assertTrue(all(row["workspace_id"] == self.ws_a["id"] for row in rows))

    def test_title_match_is_ranked_before_message_match(self):
        title_hit = self._conversation(
            self.ws_a["id"],
            "Reverse Proxy Architecture",
            "ordinary discussion",
            "title",
        )
        self._conversation(
            self.ws_a["id"],
            "Other",
            "reverse proxy architecture appears only in a message",
            "message",
        )

        rows = self.search.search(self.ws_a["id"], "Reverse Proxy Architecture")
        self.assertEqual(rows[0]["id"], title_hit["id"])
        self.assertEqual(rows[0]["match_source"], "metadata")


if __name__ == "__main__":
    unittest.main()
