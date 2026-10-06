from __future__ import annotations

import sqlite3

from kitt.core.task_plan import TaskPlanCoordinator
from kitt.evidence.episodes import TaskEpisodeService
from kitt.evidence.ledger import EventLedger
from kitt.evidence.models import (
    EvidenceConfidence,
    EvidenceCoverage,
    EvidenceKind,
    EvidenceState,
)
from kitt.extensions.plugins.api import MCPAPI
from kitt.history.database import HistoryDatabase
from kitt.history.migrations import MigrationRunner
from kitt.history.repository import HistoryRepository


def _episode(tmp_path):
    db = HistoryDatabase(str(tmp_path))
    repo = HistoryRepository(db)
    workspace = repo.get_or_create_workspace(str(tmp_path))
    conversation = repo.create_conversation(workspace["id"], "evidence-v2")
    repo.save_message(conversation["id"], "turn-1", "user", "verify evidence")
    ledger = EventLedger(db)
    service = TaskEpisodeService(db, ledger)
    episode = service.begin_turn(conversation["id"], "turn-1", "verify evidence")
    return db, conversation["id"], ledger, service, episode


def test_schema_10_migrates_evidence_metadata_to_schema_11():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schema_info(version INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO schema_info(version) VALUES(10)")
    conn.execute(
        """CREATE TABLE evidence_records(
               id TEXT PRIMARY KEY,episode_id TEXT,dimension TEXT,check_id TEXT,
               state TEXT,result TEXT,evidence_refs_json TEXT,finding_refs_json TEXT,
               created_at REAL
           )"""
    )

    MigrationRunner().migrate(conn)

    version = conn.execute("SELECT version FROM schema_info").fetchone()[0]
    columns = {row[1] for row in conn.execute("PRAGMA table_info(evidence_records)")}
    assert version == 11
    assert "metadata_json" in columns
    conn.close()


def test_evidence_v2_round_trip_and_weaker_update_does_not_downgrade(tmp_path):
    db, _, _, service, episode = _episode(tmp_path)
    strong = service.record_evidence(
        episode.id,
        "change-validation",
        "rea-static-analysis",
        EvidenceState.OUTCOME_SUPPORTED,
        result="Observed result is supported by the selected REA analysis.",
        evidence_refs=("event:rea-1",),
        kind=EvidenceKind.INFERENCE,
        authority="rea.mcp",
        confidence=EvidenceConfidence.MEDIUM,
        coverage=EvidenceCoverage.PARTIAL,
        limitations=("Runtime behavior was not observed.",),
        producer={"tool": "mcp.rea.trace_feature", "provider": "rea"},
    )
    assert len(strong.provenance_digest) == 64

    returned = service.record_evidence(
        episode.id,
        "change-validation",
        "rea-static-analysis",
        EvidenceState.EXERCISED,
        result="A later weaker observation must not replace supported evidence.",
    )
    loaded = [
        item for item in service.list_evidence(episode.id)
        if item.check_id == "rea-static-analysis"
    ][0]

    assert returned.state is EvidenceState.OUTCOME_SUPPORTED
    assert loaded.result == strong.result
    assert loaded.kind is EvidenceKind.INFERENCE
    assert loaded.authority == "rea.mcp"
    assert loaded.confidence is EvidenceConfidence.MEDIUM
    assert loaded.coverage is EvidenceCoverage.PARTIAL
    assert loaded.limitations == ("Runtime behavior was not observed.",)
    assert loaded.producer["tool"] == "mcp.rea.trace_feature"
    assert loaded.provenance_digest == strong.provenance_digest
    db.close()


def test_verification_obligations_close_only_after_host_verification(tmp_path):
    db, conversation_id, ledger, _, _ = _episode(tmp_path)
    coordinator = TaskPlanCoordinator(ledger, tmp_path)
    ledger.append_event(
        conversation_id,
        "HostToolEvidence",
        {
            "execution_id": "write-1",
            "operation": "write_file",
            "success": True,
            "mutating": True,
            "discovery": False,
            "verified": False,
            "paths": ["a.py"],
            "verification_paths": [],
            "workspace_verified": False,
            "returncode": None,
            "timed_out": False,
            "cancelled": False,
        },
        turn_id="turn-1",
    )
    first = coordinator.verification_snapshot(conversation_id, "turn-1")
    assert first["status"] == "OPEN"
    assert first["residual_unknowns"][0]["obligation_id"] == "mutations-verified"

    ledger.append_event(
        conversation_id,
        "HostToolEvidence",
        {
            "execution_id": "verify-1",
            "operation": "plan.verify",
            "success": True,
            "mutating": False,
            "discovery": False,
            "verified": True,
            "paths": [],
            "verification_paths": ["a.py"],
            "workspace_verified": False,
            "returncode": 0,
            "timed_out": False,
            "cancelled": False,
        },
        turn_id="turn-1",
    )
    closed = coordinator.verification_snapshot(conversation_id, "turn-1")
    assert closed["status"] == "READY"
    assert closed["residual_unknowns"] == []
    assert closed["obligations"][0]["status"] == "VERIFIED"
    db.close()


def test_plugin_stdio_registration_is_local_owned_and_shell_free():
    class Manager:
        def __init__(self):
            self.config = None

        def list_servers(self):
            return []

        def register_server(self, config):
            self.config = config

    manager = Manager()
    api = MCPAPI("kitt-rea", {"mcp.manage"}, manager)
    result = api.register_stdio(
        "rea",
        "/usr/local/bin/rea",
        args=["mcp"],
        env={"REA_LOG_LEVEL": "info"},
        trust="restricted",
    )

    assert result["registered"] is True
    assert result["owned"] is True
    assert manager.config.transport == "stdio"
    assert manager.config.command == "/usr/local/bin/rea"
    assert manager.config.args == ["mcp"]
    assert manager.config.env == {"REA_LOG_LEVEL": "info"}
    assert manager.config.source == "plugin:kitt-rea"
