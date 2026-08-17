"""Unit tests for Phase 3: CONSOLIDATE."""
import unittest
import time
from unittest.mock import MagicMock

from kitt.dreaming.consolidate import DreamConsolidatePhase
from kitt.dreaming.models import (
    CandidateSignal,
    DreamSnapshot,
    DreamPlan,
    MemoryRecord,
)


class TestDreamConsolidate(unittest.TestCase):
    def setUp(self):
        self.consolidator = DreamConsolidatePhase()

    def test_deterministic_exact_duplicate_creates_keep_operation(self):
        now = time.time()
        existing = MemoryRecord(
            id="mem_mvnw",
            workspace_id="ws_1",
            kind="PROJECT_RULE",
            content="Always use ./mvnw",
            normalized_content="Always use ./mvnw",
            status="ACTIVE",
            importance=0.9,
            confidence=1.0,
            created_at=now - 1000,
            updated_at=now - 1000,
        )
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(existing,),
            recent_sessions=(),
            recent_entries=(),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=now,
        )

        sig = CandidateSignal(
            id="sig_mvnw",
            source_entry_ids=("entry_1",),
            conversation_id="conv_1",
            kind_hint="PROJECT_RULE",
            raw_content="Always use ./mvnw",
            normalized_content="Always use ./mvnw",
            occurred_at=now,
            deterministic_score=0.95,
        )

        plan = self.consolidator.consolidate(snapshot, (sig,))
        self.assertEqual(len(plan.operations), 1)
        op = plan.operations[0]
        self.assertEqual(op.operation, "KEEP")
        self.assertEqual(op.source_memory_ids, ("mem_mvnw",))
        self.assertEqual(op.reason_code, "DUPLICATE")

    def test_deterministic_new_signal_creates_add_operation(self):
        now = time.time()
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(),
            recent_sessions=(),
            recent_entries=(),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=now,
        )

        sig = CandidateSignal(
            id="sig_new",
            source_entry_ids=("entry_new",),
            conversation_id="conv_1",
            kind_hint="USER_PREFERENCE",
            raw_content="User prefers PostgreSQL",
            normalized_content="User prefers PostgreSQL",
            occurred_at=now,
            deterministic_score=0.90,
        )

        plan = self.consolidator.consolidate(snapshot, (sig,))
        self.assertEqual(len(plan.operations), 1)
        op = plan.operations[0]
        self.assertEqual(op.operation, "ADD")
        self.assertEqual(op.proposed_kind, "USER_PREFERENCE")
        self.assertEqual(op.proposed_content, "User prefers PostgreSQL")

    def test_semantic_consolidation_with_mock_llm(self):
        now = time.time()
        mock_llm = MagicMock()
        mock_llm.chat.return_value = """{
            "operations": [
                {
                    "operation": "ADD",
                    "source_memory_ids": [],
                    "source_entry_ids": ["entry_123"],
                    "proposed_kind": "ARCHITECTURE_DECISION",
                    "proposed_content": "Migrated indexing to FTS5 SQLite",
                    "confidence": 0.95,
                    "reason_code": "NEWER_FACT"
                }
            ]
        }"""

        sem_consolidator = DreamConsolidatePhase(llm_client=mock_llm)
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(),
            recent_sessions=(),
            recent_entries=(),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=now,
        )

        sig = CandidateSignal(
            id="sig_fts",
            source_entry_ids=("entry_123",),
            conversation_id="conv_1",
            kind_hint="ARCHITECTURE_DECISION",
            raw_content="Migrated indexing to FTS5 SQLite",
            normalized_content="Migrated indexing to FTS5 SQLite",
            occurred_at=now,
            deterministic_score=0.90,
        )

        plan = sem_consolidator.consolidate(snapshot, (sig,))
        self.assertEqual(len(plan.operations), 1)
        op = plan.operations[0]
        self.assertEqual(op.operation, "ADD")
        self.assertEqual(op.proposed_content, "Migrated indexing to FTS5 SQLite")

    def test_semantic_consolidation_degrades_gracefully_on_llm_failure(self):
        now = time.time()
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("Ollama connection timeout")

        sem_consolidator = DreamConsolidatePhase(llm_client=mock_llm)
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(),
            recent_sessions=(),
            recent_entries=(),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=now,
        )

        sig = CandidateSignal(
            id="sig_fallback",
            source_entry_ids=("entry_fb",),
            conversation_id="conv_1",
            kind_hint="PROJECT_RULE",
            raw_content="Always use standard library first",
            normalized_content="Always use standard library first",
            occurred_at=now,
            deterministic_score=0.90,
        )

        plan = sem_consolidator.consolidate(snapshot, (sig,))
        self.assertEqual(len(plan.operations), 1)
        op = plan.operations[0]
        self.assertEqual(op.operation, "ADD")
        self.assertEqual(op.proposed_content, "Always use standard library first")


if __name__ == "__main__":
    unittest.main()
