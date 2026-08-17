"""Unit tests for Dreaming Mode domain models, validation, and hash stability."""
import unittest
import time

from kitt.dreaming.models import (
    MemoryRecord,
    MemoryEvidence,
    DreamOperation,
    DreamPlan,
    DreamRun,
    CandidateSignal,
)


class TestDreamModels(unittest.TestCase):
    def test_memory_record_creation_and_hash_stability(self):
        now = time.time()
        rec1 = MemoryRecord(
            id="mem_1",
            workspace_id="ws_1",
            kind="PROJECT_RULE",
            content="Always use standard library first",
            normalized_content="Always use standard library first",
            status="ACTIVE",
            importance=0.9,
            confidence=1.0,
            created_at=now,
            updated_at=now,
            pinned=True,
        )
        rec2 = MemoryRecord(
            id="mem_2",
            workspace_id="ws_1",
            kind="PROJECT_RULE",
            content="Always use standard library first",
            normalized_content="always use standard library first",
            status="ACTIVE",
            importance=0.9,
            confidence=1.0,
            created_at=now,
            updated_at=now,
        )
        self.assertEqual(rec1.content_hash, rec2.content_hash)
        self.assertTrue(rec1.pinned)
        self.assertEqual(rec1.kind, "PROJECT_RULE")
        self.assertEqual(rec1.status, "ACTIVE")

    def test_memory_record_validation_errors(self):
        now = time.time()
        # Invalid kind
        with self.assertRaises(ValueError):
            MemoryRecord(
                id="mem_bad",
                workspace_id="ws_1",
                kind="INVALID_KIND",  # type: ignore
                content="test",
                normalized_content="test",
                status="ACTIVE",
                importance=0.5,
                confidence=1.0,
                created_at=now,
                updated_at=now,
            )

        # Invalid status
        with self.assertRaises(ValueError):
            MemoryRecord(
                id="mem_bad",
                workspace_id="ws_1",
                kind="PROJECT_RULE",
                content="test",
                normalized_content="test",
                status="DELETED",  # type: ignore
                importance=0.5,
                confidence=1.0,
                created_at=now,
                updated_at=now,
            )

        # Invalid importance
        with self.assertRaises(ValueError):
            MemoryRecord(
                id="mem_bad",
                workspace_id="ws_1",
                kind="PROJECT_RULE",
                content="test",
                normalized_content="test",
                status="ACTIVE",
                importance=1.5,
                confidence=1.0,
                created_at=now,
                updated_at=now,
            )

    def test_dream_operation_validation(self):
        op = DreamOperation(
            operation="ADD",
            source_memory_ids=(),
            source_entry_ids=("entry_1",),
            proposed_kind="USER_PREFERENCE",
            proposed_content="User prefers PostgreSQL",
            confidence=0.95,
            reason_code="NEWER_FACT",
        )
        self.assertEqual(op.operation, "ADD")
        self.assertEqual(op.proposed_kind, "USER_PREFERENCE")

        with self.assertRaises(ValueError):
            DreamOperation(
                operation="INVALID_OP",  # type: ignore
                source_memory_ids=(),
                source_entry_ids=(),
                proposed_kind=None,
                proposed_content=None,
                confidence=0.8,
            )

    def test_candidate_signal_hash(self):
        sig = CandidateSignal(
            id="sig_1",
            source_entry_ids=("entry_1",),
            conversation_id="conv_1",
            kind_hint="USER_PREFERENCE",
            raw_content="Eu prefiro usar SQLite",
            normalized_content="Eu prefiro usar SQLite",
            occurred_at=time.time(),
            deterministic_score=0.9,
        )
        self.assertTrue(len(sig.content_hash) > 0)


if __name__ == "__main__":
    unittest.main()
