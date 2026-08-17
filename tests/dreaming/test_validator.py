"""Unit tests for DreamValidator."""
import unittest
import time

from kitt.dreaming.validator import DreamValidator
from kitt.dreaming.models import (
    DreamOperation,
    DreamPlan,
    DreamSnapshot,
    SessionDigest,
    SessionEntryDigest,
    MemoryRecord,
)


class TestDreamValidator(unittest.TestCase):
    def setUp(self):
        self.validator = DreamValidator(
            min_confidence_commit=0.90,
            min_confidence_candidate=0.70,
        )

    def test_unsupported_invention_rejected(self):
        now = time.time()
        entry = SessionEntryDigest(
            entry_id="entry_fix",
            conversation_id="conv_1",
            turn_id="turn_1",
            entry_type="USER_TURN",
            summary_text="Fix database connection pool timeout",
            created_at=now,
        )
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(),
            recent_sessions=(),
            recent_entries=(entry,),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=now,
        )

        # Proposed fake memory with zero grounding in entry
        op = DreamOperation(
            operation="ADD",
            source_memory_ids=(),
            source_entry_ids=("entry_fix",),
            proposed_kind="USER_PREFERENCE",
            proposed_content="User strongly prefers Vue over React",
            confidence=0.95,
        )
        plan = DreamPlan(operations=(op,))

        accepted, rejected = self.validator.validate_plan(plan, snapshot)
        self.assertEqual(len(accepted), 0)
        self.assertEqual(len(rejected), 1)
        self.assertIn("grounding", rejected[0][1].lower())

    def test_secret_in_proposed_content_rejected(self):
        now = time.time()
        entry = SessionEntryDigest(
            entry_id="entry_sec",
            conversation_id="conv_1",
            turn_id="turn_1",
            entry_type="USER_TURN",
            summary_text="Configured API key in settings",
            created_at=now,
        )
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(),
            recent_sessions=(),
            recent_entries=(entry,),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=now,
        )

        op = DreamOperation(
            operation="ADD",
            source_memory_ids=(),
            source_entry_ids=("entry_sec",),
            proposed_kind="TECHNICAL_FACT",
            proposed_content="API key configured: sk-1234567890abcdef1234567890abcdef",
            confidence=0.95,
        )
        plan = DreamPlan(operations=(op,))

        accepted, rejected = self.validator.validate_plan(plan, snapshot)
        self.assertEqual(len(accepted), 0)
        self.assertEqual(len(rejected), 1)
        self.assertIn("secret", rejected[0][1].lower())

    def test_pinned_memory_is_protected_from_supersession(self):
        now = time.time()
        pinned_mem = MemoryRecord(
            id="mem_pinned",
            workspace_id="ws_1",
            kind="PROJECT_RULE",
            content="Never add external dependencies without approval",
            normalized_content="Never add external dependencies without approval",
            status="ACTIVE",
            importance=1.0,
            confidence=1.0,
            created_at=now - 5000,
            updated_at=now - 5000,
            pinned=True,
        )
        entry = SessionEntryDigest(
            entry_id="entry_rule",
            conversation_id="conv_1",
            turn_id="turn_1",
            entry_type="USER_TURN",
            summary_text="Added dependency axios",
            created_at=now,
        )
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(pinned_mem,),
            recent_sessions=(),
            recent_entries=(entry,),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=now,
        )

        op = DreamOperation(
            operation="SUPERSEDE",
            source_memory_ids=("mem_pinned",),
            source_entry_ids=("entry_rule",),
            proposed_kind="PROJECT_RULE",
            proposed_content="Added dependency axios to dependencies",
            confidence=0.95,
        )
        plan = DreamPlan(operations=(op,))

        accepted, rejected = self.validator.validate_plan(plan, snapshot)
        self.assertEqual(len(accepted), 0)
        self.assertEqual(len(rejected), 1)
        self.assertIn("pinned", rejected[0][1].lower())

    def test_valid_operation_accepted(self):
        now = time.time()
        entry = SessionEntryDigest(
            entry_id="entry_valid",
            conversation_id="conv_1",
            turn_id="turn_1",
            entry_type="USER_TURN",
            summary_text="Always use standard library first in this project",
            created_at=now,
        )
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(),
            recent_sessions=(),
            recent_entries=(entry,),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=now,
        )

        op = DreamOperation(
            operation="ADD",
            source_memory_ids=(),
            source_entry_ids=("entry_valid",),
            proposed_kind="PROJECT_RULE",
            proposed_content="Always use standard library first",
            confidence=0.95,
        )
        plan = DreamPlan(operations=(op,))

        accepted, rejected = self.validator.validate_plan(plan, snapshot)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 0)


if __name__ == "__main__":
    unittest.main()
