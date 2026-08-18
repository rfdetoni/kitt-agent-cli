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

    def test_dry_run_strictly_zero_disk_and_zero_db_writes(self):
        # Create session with signals
        conv_id = "conv_dry"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO conversations (id, workspace_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (conv_id, self.workspace_id, "Dry Run Session", "COMPLETED", time.time(), time.time())
            )
        self.session_tree.append_entry(conv_id, "USER_TURN", {"content": "Always use ./mvnw"})
        self.session_tree.append_entry(conv_id, "DECISION", {"content": "Decidimos usar SQLite."})

        mem_file = self.root / ".kitt" / "memory" / "MEMORY.md"
        self.assertFalse(mem_file.exists())

        # Execute dry-run
        res = self.dream_service.dream(self.workspace_id, dry_run=True)
        self.assertTrue(res.run.dry_run)
        self.assertGreaterEqual(len(res.accepted_operations), 1)

        # Strictly ZERO writes to disk
        self.assertFalse(mem_file.exists())

        # Strictly ZERO writes to database
        self.assertEqual(len(self.memory_repo.get_all_memories(self.workspace_id)), 0)
        self.assertIsNone(self.memory_repo.get_last_dream_run(self.workspace_id))
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM memory_evidence WHERE workspace_id = ?", (self.workspace_id,))
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_cancellation_during_run(self):
        self.dream_service.cancel()
        with self.assertRaises(InterruptedError):
            self.dream_service.dream(self.workspace_id, dry_run=False)

    def test_dreaming_context_model_routing_default(self):
        from kitt.core.runtime import KittRuntime
        from kitt.core.runtime_config import RuntimeConfig
        rt = KittRuntime.build(str(self.root), config=RuntimeConfig(dream_enabled=True))
        try:
            # Verify dream_service received an LLM client configured with the context model
            self.assertIsNotNone(rt.dream_service.llm_client)
            self.assertEqual(
                rt.dream_service.llm_client.profile.model,
                rt.processor.router.config.profiles["context"].model,
            )
        finally:
            rt.close()

    def test_mutation_precedence_supersede_over_archive(self):
        # Add active unpinned memory
        mem = self.memory_repo.add_direct_memory(self.workspace_id, "Old framework preference: v1.0", kind="USER_PREFERENCE", pinned=False)
        self.assertEqual(mem.status, "ACTIVE")

        # Create session with grounding entry
        conv_id = "conv_prec"
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO conversations (id, workspace_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (conv_id, self.workspace_id, "Upgrade Session", "COMPLETED", time.time(), time.time())
            )
        entry = self.session_tree.append_entry(conv_id, "USER_TURN", {"content": "User prefers framework preference v2.0 now"})

        # Snapshot with this memory and session entry
        snapshot = self.dream_service.orient_phase.orient(self.workspace_id)
        self.assertEqual(len(snapshot.memories), 1)

        # Plan proposing SUPERSEDE for mem.id
        op = DreamOperation(
            operation="SUPERSEDE",
            source_memory_ids=(mem.id,),
            source_entry_ids=(entry.id,),
            proposed_kind="USER_PREFERENCE",
            proposed_content="User prefers framework preference v2.0 now",
            confidence=0.95,
            reason_code="NEWER_FACT",
        )
        plan = DreamPlan(operations=(op,))

        # Commit dream with this plan
        self.dream_service.consolidate_phase.consolidate = MagicMock(return_value=plan)
        res = self.dream_service.dream(self.workspace_id, dry_run=False)

        # Check in repository: old memory is SUPERSEDED, new memory is ACTIVE
        all_mems = {m.id: m for m in self.memory_repo.get_all_memories(self.workspace_id)}
        self.assertEqual(all_mems[mem.id].status, "SUPERSEDED")
        active_contents = [m.content for m in all_mems.values() if m.status == "ACTIVE"]
        self.assertTrue(len(active_contents) >= 1)
        self.assertIn("v2.0", active_contents[0])

    def test_atomic_memory_md_rebuild(self):
        self.memory_repo.add_direct_memory(self.workspace_id, "Rule: Always test before commit", kind="PROJECT_RULE")
        content = self.memory_repo.rebuild_materialized_view(self.workspace_id, root_dir=self.root)

        mem_file = self.root / ".kitt" / "memory" / "MEMORY.md"
        self.assertTrue(mem_file.exists())
        self.assertEqual(mem_file.read_text(encoding="utf-8"), content)
        self.assertIn("Always test before commit", content)

    def test_scheduler_close_no_deadlock(self):
        from kitt.core.runtime_config import RuntimeConfig
        from kitt.dreaming.scheduler import DreamScheduler

        scheduler = DreamScheduler(
            dream_service=self.dream_service,
            memory_repo=self.memory_repo,
            db=self.db,
            config=RuntimeConfig(dream_enabled=True, dream_auto_enabled=True),
            workspace_id_getter=lambda: self.workspace_id,
        )
        # Should close cleanly and safely without deadlock
        scheduler.close(timeout=1.0)
        self.assertTrue(scheduler._closed)

    def test_scheduler_cooldown_tracking(self):
        from kitt.core.runtime_config import RuntimeConfig
        from kitt.dreaming.scheduler import DreamScheduler

        scheduler = DreamScheduler(
            dream_service=self.dream_service,
            memory_repo=self.memory_repo,
            db=self.db,
            config=RuntimeConfig(dream_enabled=True, dream_auto_enabled=True, dream_min_completed_sessions=0),
            workspace_id_getter=lambda: self.workspace_id,
        )
        # First check is eligible
        self.assertTrue(scheduler.should_run(self.workspace_id))
        # Record auto attempt
        scheduler._last_auto_attempt[self.workspace_id] = time.time()
        # Immediate next check should be blocked by cooldown
        self.assertFalse(scheduler.should_run(self.workspace_id))
        scheduler.close()

    def test_repl_dream_command_dispatch(self):
        import io
        from unittest.mock import patch
        from kitt.cli.repl import KittREPL

        repl = KittREPL(root_dir=str(self.root), ui_mode="plain")
        try:
            # Test /dream --help
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                handled = repl._handle_slash_command("/dream --help")
                self.assertFalse(handled)
                output = mock_out.getvalue()
                self.assertIn("Dreaming Mode", output)
                self.assertIn("/dream --commit", output)

            # Test /dream --status
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                handled = repl._handle_slash_command("/dream --status")
                self.assertFalse(handled)
                output = mock_out.getvalue()
                self.assertIn("Dreaming Mode Status", output)
                self.assertIn("Dream model role", output)
                self.assertIn("context", output)

            # Test /dream (preview dry-run default)
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                handled = repl._handle_slash_command("/dream")
                self.assertFalse(handled)
                output = mock_out.getvalue()
                self.assertIn("Dreaming Mode — Preview", output)
                self.assertIn("Persistent changes", output)
                self.assertIn("NO", output)

            # Test /dream --commit
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                handled = repl._handle_slash_command("/dream --commit")
                self.assertFalse(handled)
                output = mock_out.getvalue()
                self.assertIn("Dreaming Mode — Consolidation Complete", output)
                self.assertIn("MEMORY.md", output)
                self.assertIn("rebuilt", output)

            # Test /dream --cancel
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                handled = repl._handle_slash_command("/dream --cancel")
                self.assertFalse(handled)
                output = mock_out.getvalue()
                self.assertIn("cancellation signal sent", output)
        finally:
            repl.runtime.close()


if __name__ == "__main__":
    unittest.main()
