from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from kitt.children.manager import ChildAgentManager
from kitt.children.repository import ChildRepository
from kitt.domain.entities import ModelProfile
from kitt.harness.refiner import HarnessRefiner
from kitt.harness.repository import HarnessRepository
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity
from kitt.llm.domain import ProviderConnectionError
from kitt.llm.failover import ProviderCircuitPool
from kitt.runtime.persistent_program import PersistentProgramSessions
from kitt.runtime.program_runtime import BoundedProgramRuntime
from kitt.runtime.state import RuntimeStateStore
from kitt.scheduling.service import PersistentWakeScheduler


class ContinuousAgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.db = HistoryDatabase(self.root)
        self.identity = resolve_workspace_identity(self.db, self.root)
        self.history = HistoryRepository(self.db)
        self.conversation = self.history.create_conversation(
            self.identity.id,
            title="continuous runtime",
        )

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_retained_agent_passivation_and_family_message(self):
        repo = ChildRepository(self.db)
        child = repo.create(
            self.conversation["id"],
            "turn-1",
            "reviewer",
            "Review the implementation",
            0,
            "context",
            [],
            [],
            512,
            30.0,
        )
        repo.update(child.id, state="RETAINED")
        manager = ChildAgentManager(
            str(self.root),
            repo,
            MagicMock(),
            workspace_id=self.identity.id,
            allow_peer_agent_messages=True,
        )
        try:
            self.assertTrue(
                manager.passivate(
                    child.id,
                    conversation_id=self.conversation["id"],
                    workspace_id=self.identity.id,
                )
            )
            message, mode = manager.send_agent_message(
                self.conversation["id"],
                sender="parent",
                recipient="child:reviewer",
                message="Inspect the final diff.",
            )
            self.assertEqual(mode, "FOLLOW_UP")
            self.assertEqual(message.recipient_id, child.id)
            revived = manager.revive(
                child.id,
                conversation_id=self.conversation["id"],
                workspace_id=self.identity.id,
            )
            self.assertEqual(revived.state, "RETAINED")
        finally:
            manager.close()

    def test_refinement_is_previewable_reversible_and_host_applied(self):
        repo = HarnessRepository(self.db)
        refiner = HarnessRefiner(repo)
        proposal = {
            "summary": "retain verified workflow",
            "rationale": "repeated validation evidence",
            "expected_outcome": "future mutations validate before completion",
            "edits": [
                {
                    "action": "create",
                    "kind": "memory",
                    "scope": "workspace",
                    "name": "verification-rule",
                    "content": "Run focused verification after repository mutations.",
                    "evidence": {"source": "contract-test"},
                }
            ],
        }
        refinement_id, preview = refiner.prepare(
            proposal,
            workspace_id=self.identity.id,
            conversation_id=self.conversation["id"],
        )
        self.assertEqual(len(preview["edits"]), 1)
        after = refiner.apply(refinement_id)
        created_id = after["created_ids"][0]
        self.assertEqual(repo.get(created_id).status, "ACTIVE")
        self.assertTrue(refiner.rollback(refinement_id))
        self.assertEqual(repo.get(created_id).status, "DELETED")

    def test_schedule_and_persistent_program_state_survive_call_boundaries(self):
        fired = []
        scheduler = PersistentWakeScheduler(
            self.db,
            lambda conversation_id, prompt, metadata: fired.append(
                (conversation_id, prompt, metadata)
            ),
        )
        schedule_id = scheduler.schedule(
            workspace_id=self.identity.id,
            conversation_id=self.conversation["id"],
            prompt="continue",
            run_at=1.0,
        )
        self.assertEqual(scheduler.run_due_once(now=2.0), 1)
        self.assertEqual(fired[0][1], "continue")
        scheduled = next(item for item in scheduler.list() if item["id"] == schedule_id)
        self.assertEqual(scheduled["state"], "COMPLETED")

        state = RuntimeStateStore(
            self.db,
            self.identity.id,
            self.conversation["id"],
        )
        sessions = PersistentProgramSessions(
            state,
            BoundedProgramRuntime(object()),
        )
        result = sessions.execute(
            "analysis",
            {
                "program": [
                    {"set": "counter", "value": 1},
                    {"return": "$counter"},
                ]
            },
            turn_id="turn-1",
            origin="MODEL",
            capabilities=set(),
            security_context=None,
        )
        self.assertTrue(result.success)
        self.assertEqual(sessions.snapshot("analysis")["counter"], 1)

    def test_provider_failover_parks_only_retryable_pre_output_failure(self):
        primary = ModelProfile(backend="openai", model="primary")
        fallback = ModelProfile(backend="openai", model="fallback")

        class FakeClient:
            def __init__(self, profile):
                self.profile = profile
                self.capabilities = None

            def chat(self, *_args, **_kwargs):
                if self.profile.model == "primary":
                    raise ProviderConnectionError("503 temporarily unavailable")
                return "fallback-ok"

            def close(self):
                return None

        pool = ProviderCircuitPool(park_seconds=5)
        client = pool.wrap(
            primary,
            [fallback],
            client_factory=FakeClient,
        )
        try:
            self.assertEqual(client.chat([{"role": "user", "content": "hello"}]), "fallback-ok")
            self.assertEqual(pool.state(primary)["failures"], 1)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
