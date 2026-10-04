from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from kitt.artifacts.store import ArtifactStore
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity


class ArtifactStoreIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.db = HistoryDatabase(self.root)
        self.identity = resolve_workspace_identity(self.db, self.root)
        self.repo = HistoryRepository(self.db)
        self.conversation = self.repo.create_conversation(
            self.identity.id,
            title="artifact integrity",
        )
        self.repo.save_message(
            self.conversation["id"],
            "turn-1",
            "user",
            "seed",
        )
        self.store = ArtifactStore(
            str(self.root),
            self.db,
            inline_limit=1,
        )

    def tearDown(self):
        self.store.close()
        self.db.close()
        self.temp.cleanup()

    def test_garbage_collection_keeps_blob_referenced_by_live_artifact(self):
        content = b"same-content-addressed-blob"
        expired = self.store.put(
            self.identity.id,
            content,
            "TEXT",
            "expired",
            conversation_id=self.conversation["id"],
            turn_id="turn-1",
            expires_at=time.time() - 1,
        )
        live = self.store.put(
            self.identity.id,
            content,
            "TEXT",
            "live",
            conversation_id=self.conversation["id"],
            turn_id="turn-1",
        )

        self.assertEqual(expired.relative_storage_path, live.relative_storage_path)
        self.assertEqual(self.store.collect_garbage(), 1)
        self.assertEqual(self.store.read(live.id), content)

    def test_turn_id_requires_a_matching_conversation(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "turn_id requires conversation_id"):
            self.store.put(
                self.identity.id,
                "content",
                "TEXT",
                "invalid",
                turn_id="turn-1",
            )


if __name__ == "__main__":
    unittest.main()
