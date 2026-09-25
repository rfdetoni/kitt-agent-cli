from __future__ import annotations

import asyncio
from pathlib import Path

from kitt.children.backends import ChildAgentBackendRegistry
from kitt.evidence.episodes import TaskEpisodeService
from kitt.evidence.efficiency import EpisodeEfficiencyService
from kitt.evidence.ledger import SessionLedger
from kitt.evidence.replay import SessionReplayService
from kitt.evidence.models import EvidenceState
from kitt.extensions.effects import EffectScope
from kitt.harness.repository import HarnessRepository
from kitt.harness.service import HarnessService
from kitt.history.database import HistoryDatabase
from kitt.history.migrations import CURRENT_SCHEMA_VERSION
from kitt.history.repository import HistoryRepository, resolve_workspace_identity
from kitt.native.coordinator import WorktreeState
from kitt.runtime.core_runtime import SafeRuntimeResult
from kitt.runtime.program_runtime import BoundedProgramRuntime


def _conversation(db: HistoryDatabase, root: str):
    repo = HistoryRepository(db)
    identity = resolve_workspace_identity(db, root)
    return identity, repo.create_conversation(identity.id, "evidence")


def test_schema_v6_installs_evidence_and_harness_tables(tmp_path: Path):
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
        assert version == CURRENT_SCHEMA_VERSION == 6
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
            "harness_presets",
            "harness_component_snapshots",
            "harness_experiments",
            "harness_experiment_arms",
            "session_replay_goldens",
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



def test_revisioned_preset_and_component_drift(tmp_path: Path):
    db = HistoryDatabase(str(tmp_path), in_memory=True)
    try:
        identity, _conversation_row = _conversation(db, str(tmp_path))
        harness = HarnessService(HarnessRepository(db))
        first = harness.ensure_preset(
            identity.id,
            "runtime-default",
            {"tools": ["repo.read"], "skills": ["caveman"]},
            activate=True,
        )
        duplicate = harness.ensure_preset(
            identity.id,
            "runtime-default",
            {"skills": ["caveman"], "tools": ["repo.read"]},
            activate=True,
        )
        second = harness.ensure_preset(
            identity.id,
            "runtime-default",
            {"tools": ["repo.read", "repo.search"], "skills": ["caveman"]},
            activate=True,
        )
        assert first.id == duplicate.id
        assert first.revision == 1
        assert second.revision == 2
        assert harness.active_preset(identity.id).id == second.id

        before = harness.capture_components(
            identity.id,
            [{"kind": "tool", "id": "repo.read", "revision": "1"}],
        )
        after = harness.capture_components(
            identity.id,
            [
                {"kind": "tool", "id": "repo.read", "revision": "2"},
                {"kind": "tool", "id": "repo.search", "revision": "1"},
            ],
        )
        diff = harness.diff_components(before["id"], after["id"])
        assert diff["has_drift"] is True
        assert [item["id"] for item in diff["added"]] == ["repo.search"]
        assert diff["changed"][0]["before"]["revision"] == "1"
        assert diff["changed"][0]["after"]["revision"] == "2"
    finally:
        db.close()


def test_controlled_experiment_uses_distinct_isolated_workspaces(tmp_path: Path):
    db = HistoryDatabase(str(tmp_path), in_memory=True)
    try:
        identity, conversation = _conversation(db, str(tmp_path))
        harness = HarnessService(HarnessRepository(db))
        baseline = harness.capture_snapshot(
            identity.id,
            conversation["id"],
            runtime_facts={"policy": "baseline"},
        )
        candidate = harness.capture_snapshot(
            identity.id,
            conversation["id"],
            runtime_facts={"policy": "candidate"},
        )

        class FakeCoordinator:
            def __init__(self):
                self.prepared = []
                self.discarded = []

            def prepare_isolated_workspace(self, owner_id, namespace="child", base_ref="HEAD"):
                path = tmp_path / f"{namespace}-{owner_id}"
                path.mkdir(parents=True, exist_ok=True)
                self.prepared.append((owner_id, str(path)))
                return WorktreeState(owner_id, str(path), f"kitt/{namespace}/{owner_id}", "READY")

            def discard_isolated_workspace(self, owner_id, delete_branch=True):
                self.discarded.append((owner_id, delete_branch))

        coordinator = FakeCoordinator()
        harness.attach_coordinator(coordinator)
        experiment_id = harness.create_experiment(
            identity.id,
            name="routing candidate",
            task={"prompt": "same task"},
            baseline_snapshot_id=baseline["id"],
            candidate_snapshot_id=candidate["id"],
            primary_metric={"id": "acceptance", "direction": "higher-is-better"},
            guardrail_metric={"id": "tokens", "direction": "lower-is-better"},
        )

        seen_paths = []
        def evaluator(arm, workspace_path, snapshot_id, task):
            seen_paths.append(workspace_path)
            assert task == {"prompt": "same task"}
            return {
                "primary_value": 0.7 if arm == "baseline" else 0.9,
                "guardrail_value": 100 if arm == "baseline" else 90,
                "validation_ok": True,
                "evidence_refs": [f"evidence:{arm}"],
                "details": {"snapshot_id": snapshot_id},
            }

        result = harness.run_experiment(experiment_id, evaluator)
        assert result["state"] == "CANDIDATE_BETTER"
        assert len(set(seen_paths)) == 2
        assert len(coordinator.discarded) == 2
        stored = harness.experiment(experiment_id)
        assert stored["state"] == "CANDIDATE_BETTER"
        assert {arm["arm"] for arm in stored["arms"]} == {"baseline", "candidate"}
    finally:
        db.close()


def test_golden_replay_detects_model_request_drift(tmp_path: Path):
    db = HistoryDatabase(str(tmp_path), in_memory=True)
    try:
        _identity, conversation = _conversation(db, str(tmp_path))
        ledger = SessionLedger(db)
        ledger.append_model_request(
            conversation["id"],
            "turn-golden",
            system_prompt="system-v1",
            messages=[{"role": "user", "content": "hello"}],
            model="test-model",
        )
        replay = SessionReplayService(ledger)
        golden = replay.save_golden(
            conversation["id"],
            "provider-boundary",
            turn_id="turn-golden",
        )
        assert golden["request_count"] == 1
        assert replay.verify_golden(
            conversation["id"],
            "provider-boundary",
        )["matches"] is True

        ledger.append_model_request(
            conversation["id"],
            "turn-golden",
            system_prompt="system-v2",
            messages=[{"role": "user", "content": "hello"}],
            model="test-model",
        )
        verified = replay.verify_golden(
            conversation["id"],
            "provider-boundary",
        )
        assert verified["matches"] is False
        assert verified["actual_request_count"] == 2
    finally:
        db.close()


def test_episode_efficiency_preserves_unobserved_telemetry(tmp_path: Path):
    db = HistoryDatabase(str(tmp_path), in_memory=True)
    try:
        _identity, conversation = _conversation(db, str(tmp_path))
        with db.get_connection() as conn:
            conn.execute(
                """INSERT INTO turns(
                       id,conversation_id,ordinal,state,mode,started_at,completed_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                ("turn-eff", conversation["id"], 1, "COMPLETED", "auto", 10.0, 12.0),
            )
        ledger = SessionLedger(db)
        episodes = TaskEpisodeService(db, ledger)
        episode = episodes.begin_turn(
            conversation["id"],
            "turn-eff",
            "Measure task efficiency",
        )
        episodes.settle_for_turn("turn-eff", "COMPLETED")
        summary = EpisodeEfficiencyService(db).summarize(episode.id)
        assert summary["telemetry_observed"] is False
        assert summary["input_tokens"] is None
        assert summary["output_tokens"] is None
        assert summary["active_duration_ms"] is None
        assert summary["wall_duration_ms"] is not None
    finally:
        db.close()


def test_learning_capture_returns_smallest_owner_hint(tmp_path: Path):
    db = HistoryDatabase(str(tmp_path), in_memory=True)
    try:
        identity, conversation = _conversation(db, str(tmp_path))
        ledger = SessionLedger(db)
        episodes = TaskEpisodeService(db, ledger)
        for ordinal in (1, 2):
            turn_id = f"turn-learn-{ordinal}"
            with db.get_connection() as conn:
                conn.execute(
                    """INSERT INTO turns(
                           id,conversation_id,ordinal,state,mode,started_at
                       ) VALUES(?,?,?,?,?,?)""",
                    (turn_id, conversation["id"], ordinal, "COMPLETED", "auto", float(ordinal)),
                )
            episode = episodes.begin_turn(
                conversation["id"],
                turn_id,
                "Keep generated code formatted",
            )
            episodes.record_evidence(
                episode.id,
                "change-validation",
                "formatter-contract",
                EvidenceState.UNOBSERVED,
                result="formatter evidence missing",
            )
            episodes.settle_for_turn(turn_id, "COMPLETED")

        harness = HarnessService(HarnessRepository(db))
        candidates = harness.learning_capture(identity.id)
        assert candidates
        assert candidates[0]["count"] == 2
        assert candidates[0]["recommended_owner"] == "quality-gate"
        assert candidates[0]["requires_intervention"] is True
    finally:
        db.close()
