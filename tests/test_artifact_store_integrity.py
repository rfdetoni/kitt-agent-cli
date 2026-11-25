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

    def test_paged_read_checks_whole_blob_without_read_bytes(self):
        from unittest.mock import patch

        content = bytes(range(256)) * 600
        artifact = self.store.put(self.identity.id, content, "TEXT", "paged")
        path = self.store.storage / artifact.relative_storage_path
        with patch.object(Path, "read_bytes", side_effect=AssertionError("full read")):
            self.assertEqual(self.store.read_page(artifact.id, 65530, 30), content[65530:65560])
            self.assertEqual(self.store.read_page(artifact.id, len(content), 30), b"")
            self.assertEqual(self.store.read_text_page(artifact.id, -1, 8)["offset"], 0)
            with path.open("r+b") as handle:
                handle.seek(-1, 2)
                handle.write(b"!")
            with self.assertRaisesRegex(ValueError, "integrity"):
                self.store.read_page(artifact.id, 0, 8)

    def test_batched_garbage_collection_preserves_live_duplicate(self):
        content = b"deduplicated"
        for i in range(130):
            self.store.put(self.identity.id, content, "TEXT", str(i), expires_at=1)
        live = self.store.put(self.identity.id, content, "TEXT", "live")
        self.assertEqual(self.store.collect_garbage(now=0), 0)
        self.assertEqual(self.store.collect_garbage(now=2), 130)
        self.assertEqual(self.store.read(live.id), content)

    def test_duplicate_blob_does_not_rewrite_valid_storage(self):
        from unittest.mock import patch

        first = self.store.put(self.identity.id, b"duplicate", "TEXT", "first")
        with patch("kitt.artifacts.store.os.replace", side_effect=AssertionError("rewrite")):
            second = self.store.put(self.identity.id, b"duplicate", "TEXT", "second")
        self.assertEqual(first.relative_storage_path, second.relative_storage_path)
        self.assertEqual(self.store.read(second.id), b"duplicate")

    def test_corrupt_duplicate_blob_is_repaired(self):
        first = self.store.put(self.identity.id, b"original", "TEXT", "first")
        (self.store.storage / first.relative_storage_path).write_bytes(b"corrupt!")
        second = self.store.put(self.identity.id, b"original", "TEXT", "second")
        self.assertEqual(self.store.read(first.id), b"original")
        self.assertEqual(self.store.read(second.id), b"original")

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
