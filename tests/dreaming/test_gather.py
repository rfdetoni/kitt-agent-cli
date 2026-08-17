"""Unit tests for Phase 2: GATHER SIGNAL."""
import unittest
import time

from kitt.dreaming.gather import DreamGatherPhase
from kitt.dreaming.models import DreamSnapshot, SessionDigest, SessionEntryDigest, MemoryRecord


class TestDreamGather(unittest.TestCase):
    def setUp(self):
        self.gather_phase = DreamGatherPhase()

    def test_gather_explicit_preference_portuguese_and_english(self):
        # 17 Aug 2026 UTC timestamp
        base_time = 1786968000.0

        session = SessionDigest(
            conversation_id="conv_1",
            started_at=base_time,
            completed_at=base_time + 100,
            user_requests=(
                "Eu prefiro usar SQLite em vez de PostgreSQL.",
                "Always use ./mvnw when running Maven commands.",
                "Não use dependências externas desnecessárias.",
            ),
            decisions=(),
            failures=(),
            validations=(),
            changed_files=(),
            entry_ids=("e1", "e2", "e3"),
        )
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(),
            recent_sessions=(session,),
            recent_entries=(),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=base_time + 200,
        )

        signals = self.gather_phase.gather(snapshot)
        self.assertEqual(len(signals), 3)

        kinds = {s.kind_hint for s in signals}
        self.assertIn("USER_PREFERENCE", kinds)
        self.assertIn("PROJECT_RULE", kinds)

    def test_gather_filters_noise_greetings(self):
        session = SessionDigest(
            conversation_id="conv_noise",
            started_at=time.time(),
            completed_at=time.time() + 10,
            user_requests=("oi", "olá, tudo bem?", "show", "ok", "thanks"),
            decisions=(),
            failures=(),
            validations=(),
            changed_files=(),
            entry_ids=("e_n",),
        )
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(),
            recent_sessions=(session,),
            recent_entries=(),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=time.time(),
        )
        signals = self.gather_phase.gather(snapshot)
        self.assertEqual(len(signals), 0)

    def test_gather_relative_date_normalization(self):
        # 17 Aug 2026 UTC
        source_time = 1786968000.0  # 2026-08-17

        session = SessionDigest(
            conversation_id="conv_date",
            started_at=source_time,
            completed_at=source_time + 10,
            user_requests=(),
            decisions=("Decidimos migrar para RepositoryIndex ontem",),
            failures=(),
            validations=(),
            changed_files=(),
            entry_ids=("e_date",),
        )
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(),
            recent_sessions=(session,),
            recent_entries=(),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=source_time + 100,
        )

        signals = self.gather_phase.gather(snapshot)
        self.assertEqual(len(signals), 1)
        sig = signals[0]
        self.assertIn("2026-08-16", sig.normalized_content)
        self.assertEqual(sig.raw_content, "Decidimos migrar para RepositoryIndex ontem")

    def test_gather_deduplicates_exact_duplicate_signals(self):
        session = SessionDigest(
            conversation_id="conv_dup",
            started_at=time.time(),
            completed_at=time.time() + 10,
            user_requests=(
                "Always use ./mvnw",
                "Always use ./mvnw",
                "always use ./mvnw",
            ),
            decisions=(),
            failures=(),
            validations=(),
            changed_files=(),
            entry_ids=("e_d1", "e_d2"),
        )
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(),
            recent_sessions=(session,),
            recent_entries=(),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=time.time(),
        )
        signals = self.gather_phase.gather(snapshot)
        self.assertEqual(len(signals), 1)


if __name__ == "__main__":
    unittest.main()
