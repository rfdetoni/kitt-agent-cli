from __future__ import annotations

import asyncio
from pathlib import Path

from kitt.children.backends import ChildAgentBackendRegistry
from kitt.evidence.episodes import TaskEpisodeService
from kitt.evidence.ledger import SessionLedger
from kitt.evidence.models import EvidenceState
from kitt.extensions.effects import EffectScope
from kitt.harness.repository import HarnessRepository
from kitt.harness.service import HarnessService
from kitt.history.database import HistoryDatabase
from kitt.history.migrations import CURRENT_SCHEMA_VERSION
from kitt.history.repository import HistoryRepository, resolve_workspace_identity
from kitt.runtime.core_runtime import SafeRuntimeResult
from kitt.runtime.program_runtime import BoundedProgramRuntime


def _conversation(db: HistoryDatabase, root: str):
    repo = HistoryRepository(db)
    identity = resolve_workspace_identity(db, root)
    return identity, repo.create_conversation(identity.id, "evidence")


def test_schema_v5_installs_evidence_and_harness_tables(tmp_path: Path):
    db = HistoryDatabase(str(tmp_path))
    try:
        with db.get_connection() as conn:
            version = conn.execute("SELECT MAX(version) FROM schema_info").fetchone()[0]
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert version == CURRENT_SCHEMA_VERSION == 5
        assert {
            "session_events",
            "session_projection_cache",
            "task_episodes",
            "task_episode_turns",
            "evidence_records",
            "episode_deliverables",
            "runtime_invariant_results",
            "harness_snapshots",
            "harness_materialization_receipts",
            "harness_interventions",
        }.issubset(tables)
    finally:
        db.close()


def test_model_request_is_reconstructible_and_projected(tmp_path: Path):
    db = HistoryDatabase(str(tmp_path), in_memory=True)
    try:
        _identity, conversation = _conversation(db, str(tmp_path))
        ledger = SessionLedger(db)
        ledger.append_model_request(
            conversation["id"],
            "turn-1",
            system_prompt="system",
            messages=[{"role": "user", "content": "hello"}],
            route="code-generation",
            profile="execute",
            model="test-model",
        )
        restored = ledger.latest_model_request(conversation["id"], "turn-1")
        assert restored is not None
        assert restored["system_prompt"] == "system"
        assert restored["messages"] == [{"role": "user", "content": "hello"}]
        projection = ledger.projections.snapshot(
            conversation["id"],
            "model_requests",
        )
        assert projection["count"] == 1
        assert projection["last_model"] == "test-model"
    finally:
        db.close()


def test_task_episode_tracks_evidence_and_terminal_state(tmp_path: Path):
    db = HistoryDatabase(str(tmp_path), in_memory=True)
    try:
        _identity, conversation = _conversation(db, str(tmp_path))
        with db.get_connection() as conn:
            conn.execute(
                """INSERT INTO turns(
                       id,conversation_id,ordinal,state,mode,started_at
                   ) VALUES(?,?,?,?,?,?)""",
                ("turn-episode", conversation["id"], 1, "CREATED", "auto", 1.0),
            )
        ledger = SessionLedger(db)
        episodes = TaskEpisodeService(db, ledger)
        episode = episodes.begin_turn(
            conversation["id"],
            "turn-episode",
            "Implement durable evidence",
        )
        episodes.record_evidence(
            episode.id,
            "change-validation",
            "relevant-check",
            EvidenceState.EXERCISED,
            result="targeted validation ran",
            evidence_refs=("test:validation",),
        )
        episodes.record_deliverables(
            episode.id,
            "turn-episode",
            ["kitt/evidence/ledger.py"],
            kind="repo.write_file",
        )
        episodes.settle_for_turn("turn-episode", "COMPLETED")
        with db.get_connection() as conn:
            state = conn.execute(
                "SELECT state FROM task_episodes WHERE id=?",
                (episode.id,),
            ).fetchone()[0]
            evidence = conn.execute(
                "SELECT state FROM evidence_records WHERE episode_id=?",
                (episode.id,),
            ).fetchall()
            deliverables = conn.execute(
                "SELECT path FROM episode_deliverables WHERE episode_id=?",
                (episode.id,),
            ).fetchall()
        assert state == "SUCCEEDED"
        assert any(row[0] == "EXERCISED" for row in evidence)
        assert [row[0] for row in deliverables] == ["kitt/evidence/ledger.py"]
    finally:
        db.close()


def test_bounded_program_runtime_composes_reads_without_code_execution():
    class FakeRuntime:
        def execute(self, operation, arguments, **_kwargs):
            if operation == "repo.search":
                return SafeRuntimeResult(
                    True,
                    operation,
                    data={"items": [{"path": "a.py"}, {"path": "b.py"}]},
                )
            if operation == "repo.read":
                return SafeRuntimeResult(
                    True,
                    operation,
                    data={"path": arguments["path"], "content": "ok"},
                )
            return SafeRuntimeResult(False, operation, error="unexpected")

    runtime = BoundedProgramRuntime(FakeRuntime())
    result = runtime.execute(
        {
            "program": [
                {
                    "call": "repo.search",
                    "arguments": {"query": "x"},
                    "save": "matches",
                },
                {
                    "for_each": "$matches.items",
                    "as": "match",
                    "do": [
                        {
                            "call": "repo.read",
                            "arguments": {"path": "$match.path"},
                            "collect": "files",
                        }
                    ],
                },
                {"return": "$files"},
            ]
        },
        turn_id="turn",
        origin="MODEL",
        capabilities=set(),
        security_context=None,
    )
    assert result.success is True
    assert result.data["tool_calls"] == 3
    assert [item["path"] for item in result.data["result"]] == ["a.py", "b.py"]

    denied = runtime.execute(
        {"program": [{"call": "process.run", "arguments": {"argv": ["echo", "x"]}}]},
        turn_id="turn",
        origin="MODEL",
        capabilities=set(),
        security_context=None,
    )
    assert denied.success is False
    assert "read-only" in denied.error


def test_harness_snapshot_and_intervention_require_comparable_evidence(tmp_path: Path):
    db = HistoryDatabase(str(tmp_path), in_memory=True)
    try:
        identity, conversation = _conversation(db, str(tmp_path))
        repository = HarnessRepository(db)
        harness = HarnessService(repository)
        harness.remember(
            "validation",
            "Run targeted tests.",
            identity.id,
            conversation["id"],
        )
        snapshot = harness.capture_snapshot(
            identity.id,
            conversation["id"],
            runtime_facts={"runtime_operations": ["repo.read"]},
        )
        harness.record_materializations(
            snapshot["id"],
            [{
                "component_kind": "runtime-operation",
                "component_id": "repo.read",
                "requested": True,
                "resolved": True,
                "materialized": True,
                "mechanism": "safe-runtime",
            }],
        )
        intervention = harness.create_intervention(
            identity.id,
            source_episode_id=None,
            asset_type="rule",
            asset_ref="validation",
            owner="agent-runtime",
            candidate_causes=[
                {"kind": "harness", "state": "supported", "evidence_refs": ["e1"]},
                {"kind": "task-complexity", "state": "candidate", "evidence_refs": ["e2"]},
            ],
            primary_metric={"id": "success", "direction": "higher-is-better"},
            guardrail_metric={"id": "cost", "direction": "lower-is-better"},
            baseline={"primary_value": 0.6, "guardrail_value": 10.0},
            comparison_window={"selection": "comparable"},
            validation={"method": "targeted tests", "evidence_refs": ["v1"]},
            stop_or_revert_condition="revert on guardrail regression",
        )
        harness.evolution.mark_applied(intervention)
        state = harness.record_intervention_result(
            intervention,
            primary_value=0.8,
            guardrail_value=9.0,
            evidence_refs=["after"],
            comparable=True,
        )
        assert state == "IMPROVING"
    finally:
        db.close()


def test_effect_scope_disposes_in_reverse_order():
    events = []
    scope = EffectScope("test")
    scope.own(lambda: events.append("first"))
    scope.own(lambda: events.append("second"))
    errors = asyncio.run(scope.dispose())
    assert errors == []
    assert events == ["second", "first"]
    assert scope.closed is True


def test_child_backend_registry_exposes_capabilities_without_enabling_execution():
    registry = ChildAgentBackendRegistry(enabled=False)
    assert "codex" in registry.names()
    capabilities = registry.capabilities("codex")
    assert capabilities.interrupt is True
    assert capabilities.continuation is False
