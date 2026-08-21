"""Canonical SQLite Schema V1 for K.I.T.T. Agent CLI (pre-1.0 modernized)."""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1

SCHEMA_V1_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schema_info (
        version INTEGER PRIMARY KEY
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS workspaces (
        id TEXT PRIMARY KEY,
        canonical_path_hash TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        git_root TEXT,
        created_at REAL NOT NULL,
        last_opened_at REAL NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        parent_conversation_id TEXT,
        forked_from_turn_id TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_turn_at REAL,
        model_context_profile TEXT,
        model_execution_profile TEXT,
        compact_summary TEXT,
        summary_version INTEGER DEFAULT 0,
        history_enabled INTEGER DEFAULT 1,
        metadata_json TEXT,
        active_entry_id TEXT,
        active_generation INTEGER NOT NULL DEFAULT 0,
        context_policy_version INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS session_entries (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        parent_entry_id TEXT,
        turn_id TEXT,
        entry_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        include_in_context INTEGER NOT NULL DEFAULT 1,
        generation INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        content_hash TEXT NOT NULL,
        metadata_json TEXT,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        FOREIGN KEY(parent_entry_id) REFERENCES session_entries(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS turns (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'CREATED',
        mode TEXT NOT NULL DEFAULT 'auto',
        user_message_id TEXT,
        assistant_message_id TEXT,
        semantic_intent TEXT,
        risk TEXT,
        confidence REAL,
        started_at REAL NOT NULL,
        completed_at REAL,
        error_code TEXT,
        changeset_id TEXT,
        parent_turn_id TEXT,
        is_compacted INTEGER DEFAULT 0,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        UNIQUE(conversation_id, ordinal)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at REAL NOT NULL,
        token_count INTEGER DEFAULT 0,
        token_count_method TEXT DEFAULT 'estimated',
        content_hash TEXT,
        is_compacted INTEGER DEFAULT 0,
        is_partial INTEGER DEFAULT 0,
        metadata_json TEXT,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        text TEXT NOT NULL,
        type TEXT NOT NULL,
        source_span TEXT,
        created_at REAL NOT NULL,
        active INTEGER DEFAULT 1,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_files (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        rel_path TEXT NOT NULL,
        relation TEXT NOT NULL,
        file_hash TEXT,
        created_at REAL NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS pending_actions (
        id TEXT PRIMARY KEY,
        approval_request_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        normalized_args_json TEXT NOT NULL,
        action_hash TEXT NOT NULL,
        source_response_sha256 TEXT NOT NULL,
        affected_paths_json TEXT NOT NULL,
        before_hashes_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending',
        security_context_json TEXT DEFAULT '{}',
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS telemetry_events (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        route TEXT NOT NULL,
        start_time REAL NOT NULL,
        duration_ms REAL NOT NULL,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        tokens_saved INTEGER DEFAULT 0,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        normalized_content TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        importance REAL NOT NULL DEFAULT 0.5,
        confidence REAL NOT NULL DEFAULT 1.0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_accessed_at REAL,
        access_count INTEGER NOT NULL DEFAULT 0,
        valid_from REAL,
        valid_until REAL,
        supersedes_id TEXT,
        content_hash TEXT NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT DEFAULT '{}',
        FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_evidence (
        id TEXT PRIMARY KEY,
        memory_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        session_entry_id TEXT,
        conversation_id TEXT,
        source_kind TEXT NOT NULL,
        evidence_text TEXT NOT NULL,
        created_at REAL NOT NULL,
        FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE,
        FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS dream_runs (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        started_at REAL NOT NULL,
        finished_at REAL,
        status TEXT NOT NULL,
        sessions_scanned INTEGER NOT NULL DEFAULT 0,
        entries_scanned INTEGER NOT NULL DEFAULT 0,
        signals_found INTEGER NOT NULL DEFAULT 0,
        memories_added INTEGER NOT NULL DEFAULT 0,
        memories_merged INTEGER NOT NULL DEFAULT 0,
        memories_superseded INTEGER NOT NULL DEFAULT 0,
        memories_archived INTEGER NOT NULL DEFAULT 0,
        model TEXT NOT NULL DEFAULT '',
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        failure_reason TEXT,
        dry_run INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS daemon_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        client_id TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS pairing_sessions (
        session_id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        last_seen_at REAL NOT NULL,
        client_addr TEXT,
        user_agent TEXT,
        is_active INTEGER NOT NULL DEFAULT 1
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        conversation_id TEXT,
        turn_id TEXT,
        artifact_type TEXT NOT NULL,
        storage_kind TEXT NOT NULL,
        relative_storage_path TEXT,
        inline_content BLOB,
        summary TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        sensitivity TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL,
        pinned INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT,
        FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS harness_entries (
        id TEXT PRIMARY KEY,
        workspace_id TEXT,
        conversation_id TEXT,
        entry_kind TEXT NOT NULL,
        scope TEXT NOT NULL,
        name TEXT NOT NULL,
        content TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        confidence REAL NOT NULL,
        status TEXT NOT NULL,
        version INTEGER NOT NULL,
        supersedes_id TEXT,
        content_hash TEXT NOT NULL,
        created_at REAL NOT NULL,
        created_by TEXT NOT NULL,
        FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS harness_refinements (
        id TEXT PRIMARY KEY,
        conversation_id TEXT,
        proposal_json TEXT NOT NULL,
        before_snapshot_json TEXT NOT NULL,
        after_snapshot_json TEXT,
        state TEXT NOT NULL,
        created_at REAL NOT NULL,
        applied_at REAL,
        rolled_back_at REAL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS goals (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        objective TEXT NOT NULL,
        state TEXT NOT NULL,
        token_budget INTEGER,
        max_turns INTEGER NOT NULL,
        max_wall_seconds INTEGER NOT NULL,
        tokens_used INTEGER NOT NULL DEFAULT 0,
        turns_used INTEGER NOT NULL DEFAULT 0,
        continuations_used INTEGER NOT NULL DEFAULT 0,
        success_criteria_json TEXT NOT NULL,
        started_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        completed_at REAL,
        last_error TEXT,
        scheduled_at REAL,
        next_run_at REAL,
        recurrence TEXT,
        heartbeat_enabled INTEGER DEFAULT 0,
        last_heartbeat_at REAL,
        heartbeat_interval_seconds INTEGER DEFAULT 60,
        resume_policy TEXT DEFAULT 'manual',
        retry_policy TEXT DEFAULT '{\"max_retries\": 3, \"retry_count\": 0}',
        owner_session_id TEXT,
        lease_id TEXT,
        lease_expires_at REAL,
        max_cost REAL DEFAULT 0.0,
        max_failures INTEGER DEFAULT 5,
        max_retries INTEGER DEFAULT 3,
        max_children INTEGER DEFAULT 5,
        failures_used INTEGER DEFAULT 0,
        retries_used INTEGER DEFAULT 0,
        lease_owner_id TEXT,
        lease_heartbeat_at REAL,
        cost_used REAL DEFAULT 0.0,
        children_used INTEGER DEFAULT 0,
        capabilities_json TEXT DEFAULT '[]',
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_gates (
        id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL,
        argv_json TEXT NOT NULL,
        workspace_hash TEXT,
        last_exit_code INTEGER,
        last_output_artifact_id TEXT,
        last_run_at REAL,
        status TEXT NOT NULL,
        name TEXT DEFAULT 'QualityGate',
        timeout_seconds INTEGER DEFAULT 120,
        FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS child_sessions (
        id TEXT PRIMARY KEY,
        parent_conversation_id TEXT NOT NULL,
        parent_turn_id TEXT NOT NULL,
        name TEXT NOT NULL,
        task TEXT NOT NULL,
        state TEXT NOT NULL,
        depth INTEGER NOT NULL,
        model_profile TEXT NOT NULL,
        allowed_paths_json TEXT NOT NULL,
        enabled_tools_json TEXT NOT NULL,
        token_budget INTEGER NOT NULL,
        tokens_used INTEGER NOT NULL DEFAULT 0,
        timeout_seconds INTEGER NOT NULL,
        result_artifact_id TEXT,
        error TEXT,
        created_at REAL NOT NULL,
        started_at REAL,
        completed_at REAL,
        current_task_id TEXT,
        task_started_at REAL,
        capabilities_json TEXT DEFAULT '[]',
        context_summary TEXT DEFAULT '',
        runtime_conversation_id TEXT,
        security_context_json TEXT DEFAULT '{}',
        FOREIGN KEY(parent_conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS child_messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        parent_id TEXT NOT NULL,
        child_id TEXT NOT NULL,
        sender_id TEXT NOT NULL,
        recipient_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'SENT',
        timestamp REAL NOT NULL,
        correlation_id TEXT,
        reply_to TEXT,
        trace_id TEXT,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS child_worktrees (
        child_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        path TEXT NOT NULL,
        branch TEXT NOT NULL,
        base_ref TEXT NOT NULL,
        state TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_error TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS retained_child_agents (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        parent_conversation_id TEXT NOT NULL,
        role TEXT NOT NULL,
        capabilities_json TEXT NOT NULL,
        state TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_active_at REAL NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS executable_skills (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        name TEXT NOT NULL,
        code TEXT NOT NULL,
        schema_json TEXT NOT NULL,
        is_trusted INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        UNIQUE(workspace_id, name)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        cron_expr TEXT,
        interval_seconds REAL,
        prompt TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'ACTIVE',
        last_run_at REAL,
        next_run_at REAL NOT NULL,
        created_at REAL NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS approval_requests (
        approval_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        arguments_hash TEXT NOT NULL,
        scope_json TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('PENDING','GRANTED','CONSUMED','DENIED','EXPIRED','FAILED')),
        nonce_hash TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        decided_at TEXT,
        consumed_at TEXT,
        decision_source TEXT,
        failure_reason TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS remembered_approval_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tool_name TEXT NOT NULL,
        path_glob TEXT,
        decision TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS consumed_approval_nonces (
        nonce TEXT PRIMARY KEY,
        approval_id TEXT NOT NULL,
        consumed_at REAL NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS compactions (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        entry_id TEXT NOT NULL,
        first_compacted_entry_id TEXT NOT NULL,
        first_kept_entry_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        tokens_before INTEGER NOT NULL,
        tokens_after INTEGER NOT NULL,
        policy_version INTEGER NOT NULL,
        model_profile TEXT,
        validation_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        FOREIGN KEY(entry_id) REFERENCES session_entries(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS queued_inputs (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('STEERING','FOLLOW_UP')),
        content TEXT NOT NULL,
        position INTEGER NOT NULL,
        status TEXT NOT NULL,
        target_generation INTEGER NOT NULL,
        created_at REAL NOT NULL,
        delivered_at REAL,
        content_hash TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        UNIQUE(conversation_id, kind, position)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS usage_attributions (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        turn_id TEXT,
        child_session_id TEXT,
        stage TEXT NOT NULL,
        provider TEXT,
        model TEXT,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        estimated INTEGER NOT NULL,
        duration_ms REAL NOT NULL,
        created_at REAL NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_states (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        state_key TEXT NOT NULL,
        value_json TEXT NOT NULL,
        bytes_count INTEGER NOT NULL,
        ttl_seconds REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        expires_at REAL,
        UNIQUE(workspace_id, conversation_id, state_key)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_concepts (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        name TEXT NOT NULL,
        definition TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0.5,
        revision INTEGER NOT NULL DEFAULT 1,
        labels_json TEXT NOT NULL DEFAULT '[]',
        source_memory_ids_json TEXT NOT NULL DEFAULT '[]',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(workspace_id, name)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_links (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        relation TEXT NOT NULL,
        weight REAL NOT NULL DEFAULT 1.0,
        created_at REAL NOT NULL,
        UNIQUE(workspace_id, source_id, target_id, relation),
        CHECK(source_id <> target_id),
        FOREIGN KEY(source_id) REFERENCES knowledge_concepts(id) ON DELETE CASCADE,
        FOREIGN KEY(target_id) REFERENCES knowledge_concepts(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS correction_memories (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        context TEXT NOT NULL,
        predicted TEXT NOT NULL,
        corrected TEXT NOT NULL,
        reason TEXT,
        source TEXT NOT NULL DEFAULT 'user',
        applied_count INTEGER NOT NULL DEFAULT 0,
        vector_json TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS native_memory_vectors (
        memory_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        vector_json TEXT NOT NULL,
        dimensions INTEGER NOT NULL,
        encoder TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS edit_changesets (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        description TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'APPLIED'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS edit_change_snapshots (
        changeset_id TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        existed INTEGER NOT NULL,
        content TEXT,
        post_exists INTEGER NOT NULL DEFAULT 1,
        post_sha256 TEXT,
        post_content TEXT,
        PRIMARY KEY(changeset_id, relative_path),
        FOREIGN KEY(changeset_id) REFERENCES edit_changesets(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS coordination_leases (
        workspace_id TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('READ','WRITE')),
        intent TEXT NOT NULL,
        lease_token TEXT NOT NULL,
        acquired_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        PRIMARY KEY(workspace_id, resource_id, owner_id)
    );
    """,
    # Indices
    "CREATE INDEX IF NOT EXISTS idx_session_entries_conversation_generation ON session_entries(conversation_id, generation, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_session_entries_parent ON session_entries(parent_entry_id);",
    "CREATE INDEX IF NOT EXISTS idx_memories_ws_status ON memories(workspace_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_memories_content_hash ON memories(workspace_id, content_hash);",
    "CREATE INDEX IF NOT EXISTS idx_evidence_memory_id ON memory_evidence(memory_id);",
    "CREATE INDEX IF NOT EXISTS idx_evidence_session_entry ON memory_evidence(session_entry_id);",
    "CREATE INDEX IF NOT EXISTS idx_dream_runs_ws_started ON dream_runs(workspace_id, started_at);",
    "CREATE INDEX IF NOT EXISTS idx_daemon_events_session ON daemon_events(session_id, id ASC);",
    "CREATE INDEX IF NOT EXISTS idx_messages_conv_created ON messages(conversation_id, created_at DESC, id DESC);",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_conversation_created ON artifacts(conversation_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_child_sessions_parent ON child_sessions(parent_conversation_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_child_messages_conv ON child_messages(conversation_id, timestamp ASC);",
    "CREATE INDEX IF NOT EXISTS idx_queued_inputs_pending ON queued_inputs(conversation_id, status, kind, position);",
    "CREATE INDEX IF NOT EXISTS idx_approval_ws_state_req ON approval_requests(workspace_id, state, conversation_id, requested_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_pending_actions_app_req ON pending_actions(approval_request_id, state);",
    "CREATE INDEX IF NOT EXISTS idx_edit_changesets_ws_conv ON edit_changesets(workspace_id, conversation_id, created_at DESC, id DESC);",
    "CREATE INDEX IF NOT EXISTS idx_edit_change_snapshots_changeset ON edit_change_snapshots(changeset_id, relative_path ASC);",
    "CREATE INDEX IF NOT EXISTS idx_coordination_leases_expiry ON coordination_leases(workspace_id, expires_at);",
]


class IncompatibleSchemaError(RuntimeError):
    """Raised when an incompatible database schema is detected."""


class MigrationRunner:
    """Manages SQLite schema creation and validation for K.I.T.T."""

    def __init__(self):
        self.target_version = CURRENT_SCHEMA_VERSION

    def get_current_version(self, conn: sqlite3.Connection) -> int:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_info';"
        )
        if not cur.fetchone():
            return 0
        cur.execute("SELECT version FROM schema_info LIMIT 1;")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def migrate(self, conn: sqlite3.Connection) -> None:
        current_version = self.get_current_version(conn)

        if current_version == self.target_version:
            return

        if current_version > self.target_version:
            raise IncompatibleSchemaError(
                f"Database schema version {current_version} is newer than supported version {self.target_version}. "
                "Run: kitt doctor --reset-state"
            )

        if current_version != 0:
            # Legacy pre-1.0 database detected; do not silently migrate
            raise IncompatibleSchemaError(
                f"State schema version {current_version} is incompatible with this development build. "
                "Run: kitt doctor --reset-state"
            )

        # Fresh database: initialize schema v1 in a single transaction
        with conn:
            for statement in SCHEMA_V1_STATEMENTS:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_info (version) VALUES (?);", (CURRENT_SCHEMA_VERSION,)
            )
            logger.info("Initialized KITT SQLite schema version %d", CURRENT_SCHEMA_VERSION)
