"""Unit tests for Dreaming Mode transactions, security, privacy, and idempotency."""
import unittest
from pathlib import Path
import tempfile
import time
from unittest.mock import MagicMock

from kitt.dreaming.models import (
    CandidateSignal,
    DreamOperation,
    DreamPlan,
    DreamRun,
    MemoryEvidence,
    MemoryRecord,
)
from kitt.dreaming.repository import MemoryRepository
from kitt.dreaming.service import DreamingService
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity
from kitt.history.session_tree import SessionTreeRepository
from kitt.security.egress import EgressPolicy


class TestDreamTransactionsAndSecurity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = HistoryDatabase(str(self.root), in_memory=True)
        self.identity = resolve_workspace_identity(self.db, str(self.root))
        self.workspace_id = self.identity.id
        self.history_repo = HistoryRepository(self.db)
        self.session_tree = SessionTreeRepository(self.db)
        self.memory_repo = MemoryRepository(self.db)

        self.dream_service = DreamingService(
            db=self.db,
            memory_repo=self.memory_repo,
            history_repo=self.history_repo,
            session_tree=self.session_tree,
            root_dir=self.root,
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_transactional_rollback_on_commit_failure(self):
        # Insert initial memory
        initial = self.memory_repo.add_direct_memory(self.workspace_id, "Initial memory", kind="PROJECT_RULE")
        self.assertEqual(len(self.memory_repo.get_all_memories(self.workspace_id)), 1)

        # Attempt commit_dream with an invalid memory record that fails SQLite PRIMARY KEY uniqueness
        bad_mem = MemoryRecord(
            id=initial.id,
            workspace_id=self.workspace_id,
            kind="TECHNICAL_FACT",
            content="Some conflicting fact",
            normalized_content="Some conflicting fact",
            status="ACTIVE",
            importance=0.5,
            confidence=1.0,
            created_at=time.time(),
            updated_at=time.time(),
        )
        dream_run = DreamRun(
            id="run_fail",
            workspace_id=self.workspace_id,
            started_at=time.time(),
            finished_at=time.time(),
            status="RUNNING",
            sessions_scanned=1,
            entries_scanned=1,
            signals_found=1,
            memories_added=1,
            memories_merged=0,
            memories_superseded=0,
            memories_archived=0,
            model="test",
            input_tokens=0,
            output_tokens=0,
        )

        with self.assertRaises(Exception):
            self.memory_repo.commit_dream(
                self.workspace_id,
                dream_run=dream_run,
                new_memories=[bad_mem],
                updated_memories=[],
                new_evidence=[],
            )

        # DB must remain completely unchanged
        mems = self.memory_repo.get_all_memories(self.workspace_id)
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0].id, initial.id)

    def test_egress_policy_privacy_mode_offline_prevents_remote_llm(self):
        remote_mock_llm = MagicMock()
        remote_mock_llm.profile.backend = "openai"

        egress_offline = EgressPolicy(mode="offline")
        service = DreamingService(
            db=self.db,
            memory_repo=self.memory_repo,
            history_repo=self.history_repo,
            session_tree=self.session_tree,
            root_dir=self.root,
            llm_client=remote_mock_llm,
            egress_policy=egress_offline,
        )

        # Create session with preference signal
        conv_id = "conv_offline"
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO conversations (id, workspace_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (conv_id, self.workspace_id, "Offline Session", "COMPLETED", time.time(), time.time()))
        self.session_tree.append_entry(conv_id, "USER_TURN", {"content": "Eu prefiro usar SQLite."})

        # Run dream
        result = service.dream(self.workspace_id, dry_run=True)

        # Remote LLM client must NOT have been called
        self.assertEqual(remote_mock_llm.chat.call_count, 0)
        # But deterministic dream successfully extracted and proposed the addition
        self.assertEqual(len(result.accepted_operations), 1)

    def test_idempotent_dream_runs(self):
        conv_id = "conv_idem"
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO conversations (id, workspace_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (conv_id, self.workspace_id, "Idempotent Session", "COMPLETED", time.time(), time.time()))
        self.session_tree.append_entry(conv_id, "USER_TURN", {"content": "Always use ./mvnw"})

        # First run -> adds memory
        res1 = self.dream_service.dream(self.workspace_id, dry_run=False)
        self.assertEqual(res1.run.memories_added, 1)

        # Second run with no new sessions -> 0 adds
        res2 = self.dream_service.dream(self.workspace_id, dry_run=False)
        self.assertEqual(res2.run.memories_added, 0)


if __name__ == "__main__":
    unittest.main()
