from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kitt.artifacts.store import ArtifactStore
from kitt.core.workspace_snapshot import WorkspaceSnapshotService
from kitt.evidence.ledger import EventLedger
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity


class WorkspaceRecoveryAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.db = HistoryDatabase(self.root)
        self.identity = resolve_workspace_identity(self.db, self.root)
        self.repo = HistoryRepository(self.db)
        self.conversation = self.repo.create_conversation(
            self.identity.id,
            "recovery acceptance",
        )
        self.ledger = EventLedger(self.db)
        self.artifacts = ArtifactStore(
            str(self.root),
            self.db,
            inline_limit=64,
            page_bytes=1024,
        )
        self.snapshots = WorkspaceSnapshotService(
            self.root,
            workspace_id=self.identity.id,
            artifact_store=self.artifacts,
            ledger=self.ledger,
        )

    def tearDown(self):
        self.artifacts.close()
        self.db.close()
        self.temp.cleanup()

    def test_selective_rollback_restores_only_requested_path(self):
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_text("before-first\n", encoding="utf-8")
        second.write_text("before-second\n", encoding="utf-8")

        snapshot = self.snapshots.capture(
            conversation_id=self.conversation["id"],
            turn_id="turn-selective",
            paths=["first.txt", "second.txt"],
        )
        first.write_text("after-first\n", encoding="utf-8")
        second.write_text("after-second\n", encoding="utf-8")

        diff = self.snapshots.diff(
            snapshot.snapshot_id,
            conversation_id=self.conversation["id"],
            turn_id="turn-selective",
            paths=["first.txt"],
        )
        expected_current = {
            item["path"]: item["current_sha256"]
            for item in diff
        }
        restored = self.snapshots.restore(
            snapshot.snapshot_id,
            conversation_id=self.conversation["id"],
            turn_id="turn-selective",
            paths=["first.txt"],
            expected_current=expected_current,
        )

        self.assertEqual(restored, ["first.txt"])
        self.assertEqual(first.read_text(encoding="utf-8"), "before-first\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "after-second\n")

    def test_snapshot_recovers_exact_bytes_after_service_reconstruction(self):
        target = self.root / "binary.dat"
        original = (b"\x00KITT\xff" * 7000) + b"exact-tail"
        target.write_bytes(original)

        snapshot = self.snapshots.capture(
            conversation_id=self.conversation["id"],
            turn_id="turn-exact",
            paths=["binary.dat"],
        )
        target.write_bytes(b"mutated")

        recovered_service = WorkspaceSnapshotService(
            self.root,
            workspace_id=self.identity.id,
            artifact_store=self.artifacts,
            ledger=self.ledger,
        )
        restored = recovered_service.restore(
            snapshot.snapshot_id,
            conversation_id=self.conversation["id"],
            turn_id="turn-exact",
            paths=["binary.dat"],
        )

        self.assertEqual(restored, ["binary.dat"])
        self.assertEqual(target.read_bytes(), original)

    def test_large_artifact_query_and_page_reads_remain_bounded(self):
        marker = "needle-acceptance"
        content = (
            ("prefix-" * 3000)
            + marker
            + ("-middle-" * 3000)
            + marker
            + ("-suffix" * 3000)
        )
        artifact = self.artifacts.put(
            self.identity.id,
            content,
            "TEXT",
            "large query artifact",
            conversation_id=self.conversation["id"],
            turn_id="turn-query",
        )

        page = self.artifacts.read_text_page(
            artifact.id,
            offset=0,
            max_bytes=100_000,
        )
        hits = self.artifacts.search_text(
            artifact.id,
            marker,
            limit=1,
            context_chars=80,
        )

        self.assertEqual(page["bytes_returned"], 1024)
        self.assertTrue(page["has_more"])
        self.assertEqual(len(hits), 1)
        self.assertIn(marker, hits[0]["excerpt"])
        self.assertEqual(hits[0]["content_hash"], artifact.content_hash)


if __name__ == "__main__":
    unittest.main()
