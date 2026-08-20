import sqlite3
import time
from dataclasses import dataclass
from typing import Tuple

@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: Tuple[str, ...]

MIGRATIONS = [
    Migration(
        version=1,
        name="initial_v1_migration",
        statements=(
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
            CREATE TABLE IF NOT EXISTS session_entries (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                parent_entry_id TEXT,
                turn_id TEXT,
                entry_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                include_in_context INTEGER NOT NULL DEFAULT 1,
                generation INTEGER NOT NULL,
                created_at REAL NOT NULL,
                content_hash TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY(parent_entry_id) REFERENCES session_entries(id)
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_session_entries_conversation_generation
            ON session_entries(conversation_id, generation, created_at);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_session_entries_parent
            ON session_entries(parent_entry_id);
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
            CREATE INDEX IF NOT EXISTS idx_artifacts_conversation_created
            ON artifacts(conversation_id, created_at DESC);
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
                FOREIGN KEY(parent_conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_child_sessions_parent
            ON child_sessions(parent_conversation_id, created_at DESC);
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
            """
        )
    )
]

# Versions 2-4 deliberately repeat CREATE IF NOT EXISTS declarations.  This is
# required for databases created by the pre-Prime v1 release, whose schema_info
# says "1" although none of the Prime tables existed yet.
MIGRATIONS.extend([
    Migration(2, "session_tree_and_artifacts", (
        "ALTER TABLE conversations ADD COLUMN active_entry_id TEXT;",
        "ALTER TABLE conversations ADD COLUMN active_generation INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE conversations ADD COLUMN context_policy_version INTEGER NOT NULL DEFAULT 1;",
        """CREATE TABLE IF NOT EXISTS session_entries (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, parent_entry_id TEXT, turn_id TEXT, entry_type TEXT NOT NULL, payload_json TEXT NOT NULL, include_in_context INTEGER NOT NULL DEFAULT 1, generation INTEGER NOT NULL, created_at REAL NOT NULL, content_hash TEXT NOT NULL, FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE, FOREIGN KEY(parent_entry_id) REFERENCES session_entries(id));""",
        """CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, conversation_id TEXT, turn_id TEXT, artifact_type TEXT NOT NULL, storage_kind TEXT NOT NULL, relative_storage_path TEXT, inline_content BLOB, summary TEXT NOT NULL, content_hash TEXT NOT NULL, size_bytes INTEGER NOT NULL, sensitivity TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL, pinned INTEGER NOT NULL DEFAULT 0, metadata_json TEXT, FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE, FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);""",
        """CREATE TABLE IF NOT EXISTS compactions (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, entry_id TEXT NOT NULL, first_compacted_entry_id TEXT NOT NULL, first_kept_entry_id TEXT NOT NULL, summary TEXT NOT NULL, tokens_before INTEGER NOT NULL, tokens_after INTEGER NOT NULL, policy_version INTEGER NOT NULL, model_profile TEXT, validation_json TEXT NOT NULL, created_at REAL NOT NULL, FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE, FOREIGN KEY(entry_id) REFERENCES session_entries(id));""",
    )),
    Migration(3, "prime_services", (
        """CREATE TABLE IF NOT EXISTS queued_inputs (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('STEERING','FOLLOW_UP')), content TEXT NOT NULL, position INTEGER NOT NULL, status TEXT NOT NULL, target_generation INTEGER NOT NULL, created_at REAL NOT NULL, delivered_at REAL, content_hash TEXT NOT NULL, FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE, UNIQUE(conversation_id, kind, position));""",
        """CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, objective TEXT NOT NULL, state TEXT NOT NULL, token_budget INTEGER, max_turns INTEGER NOT NULL, max_wall_seconds INTEGER NOT NULL, tokens_used INTEGER NOT NULL DEFAULT 0, turns_used INTEGER NOT NULL DEFAULT 0, continuations_used INTEGER NOT NULL DEFAULT 0, success_criteria_json TEXT NOT NULL, started_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL, last_error TEXT, FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);""",
        """CREATE TABLE IF NOT EXISTS quality_gates (id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, argv_json TEXT NOT NULL, workspace_hash TEXT, last_exit_code INTEGER, last_output_artifact_id TEXT, last_run_at REAL, status TEXT NOT NULL, name TEXT DEFAULT 'QualityGate', timeout_seconds INTEGER DEFAULT 120, FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE);""",
        """CREATE TABLE IF NOT EXISTS harness_entries (id TEXT PRIMARY KEY, workspace_id TEXT, conversation_id TEXT, entry_kind TEXT NOT NULL, scope TEXT NOT NULL, name TEXT NOT NULL, content TEXT NOT NULL, evidence_json TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL, supersedes_id TEXT, content_hash TEXT NOT NULL, created_at REAL NOT NULL, created_by TEXT NOT NULL, FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE, FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);""",
        """CREATE TABLE IF NOT EXISTS harness_refinements (id TEXT PRIMARY KEY, conversation_id TEXT, proposal_json TEXT NOT NULL, before_snapshot_json TEXT NOT NULL, after_snapshot_json TEXT, state TEXT NOT NULL, created_at REAL NOT NULL, applied_at REAL, rolled_back_at REAL);""",
        """CREATE TABLE IF NOT EXISTS child_sessions (id TEXT PRIMARY KEY, parent_conversation_id TEXT NOT NULL, parent_turn_id TEXT NOT NULL, name TEXT NOT NULL, task TEXT NOT NULL, state TEXT NOT NULL, depth INTEGER NOT NULL, model_profile TEXT NOT NULL, allowed_paths_json TEXT NOT NULL, enabled_tools_json TEXT NOT NULL, token_budget INTEGER NOT NULL, tokens_used INTEGER NOT NULL DEFAULT 0, timeout_seconds INTEGER NOT NULL, result_artifact_id TEXT, error TEXT, created_at REAL NOT NULL, started_at REAL, completed_at REAL, FOREIGN KEY(parent_conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);""",
        """CREATE TABLE IF NOT EXISTS usage_attributions (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, turn_id TEXT, child_session_id TEXT, stage TEXT NOT NULL, provider TEXT, model TEXT, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, estimated INTEGER NOT NULL, duration_ms REAL NOT NULL, created_at REAL NOT NULL);""",
    )),
    Migration(4, "integrity_and_indexes", (
        """CREATE TABLE IF NOT EXISTS consumed_approval_nonces (nonce TEXT PRIMARY KEY, approval_id TEXT NOT NULL, consumed_at REAL NOT NULL);""",
        "CREATE INDEX IF NOT EXISTS idx_session_entries_conversation_generation ON session_entries(conversation_id, generation, created_at);",
        "CREATE INDEX IF NOT EXISTS idx_session_entries_parent ON session_entries(parent_entry_id);",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_conversation_created ON artifacts(conversation_id, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_child_sessions_parent ON child_sessions(parent_conversation_id, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_queued_inputs_pending ON queued_inputs(conversation_id, status, kind, position);",
    )),
    Migration(5, "quality_gate_name_and_timeout", (
        """CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, objective TEXT NOT NULL, state TEXT NOT NULL, token_budget INTEGER, max_turns INTEGER NOT NULL, max_wall_seconds INTEGER NOT NULL, tokens_used INTEGER NOT NULL DEFAULT 0, turns_used INTEGER NOT NULL DEFAULT 0, continuations_used INTEGER NOT NULL DEFAULT 0, success_criteria_json TEXT NOT NULL, started_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL, last_error TEXT, FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);""",
        """CREATE TABLE IF NOT EXISTS quality_gates (id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, argv_json TEXT NOT NULL, workspace_hash TEXT, last_exit_code INTEGER, last_output_artifact_id TEXT, last_run_at REAL, status TEXT NOT NULL, name TEXT DEFAULT 'QualityGate', timeout_seconds INTEGER DEFAULT 120, FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE);""",
    )),
])

# Some development builds recorded versions 2-5 without applying every Prime
# table. Re-run their idempotent declarations once for those partial databases.
MIGRATIONS.append(Migration(
    6,
    "repair_partial_prime_schema",
    tuple(statement for migration in MIGRATIONS if 2 <= migration.version <= 5 for statement in migration.statements),
))
MIGRATIONS.append(Migration(
    7,
    "remembered_approval_rules",
    (
        """CREATE TABLE IF NOT EXISTS remembered_approval_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, tool_name TEXT NOT NULL, path_glob TEXT, decision TEXT NOT NULL, created_at REAL NOT NULL);""",
    )
))
MIGRATIONS.append(Migration(
    8,
    "persistent_approval_requests",
    (
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
        CREATE INDEX IF NOT EXISTS idx_approval_state_expiry
        ON approval_requests(state, expires_at);
        """
    )
))

MIGRATIONS.append(Migration(
    version=9,
    name="dreaming_and_durable_memory_v9",
    statements=(
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
        CREATE INDEX IF NOT EXISTS idx_memories_ws_status
        ON memories(workspace_id, status);
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_memories_content_hash
        ON memories(workspace_id, content_hash);
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
        CREATE INDEX IF NOT EXISTS idx_evidence_memory_id
        ON memory_evidence(memory_id);
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_evidence_session_entry
        ON memory_evidence(session_entry_id);
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
        CREATE INDEX IF NOT EXISTS idx_dream_runs_ws_started
        ON dream_runs(workspace_id, started_at);
        """
    )
))

MIGRATIONS.append(Migration(
    version=10,
    name="prime_agent_persistence_v10",
    statements=(
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
            FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            UNIQUE(workspace_id, conversation_id, state_key)
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_states_conv_key
        ON runtime_states(conversation_id, state_key);
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_states_expires
        ON runtime_states(expires_at);
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
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_child_messages_conv
        ON child_messages(conversation_id, timestamp);
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_child_messages_recipient
        ON child_messages(recipient_id, status);
        """,
        """
        CREATE TABLE IF NOT EXISTS daemon_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_daemon_events_session_id
        ON daemon_events(session_id, id);
        """,
        "ALTER TABLE goals ADD COLUMN scheduled_at REAL;",
        "ALTER TABLE goals ADD COLUMN next_run_at REAL;",
        "ALTER TABLE goals ADD COLUMN recurrence TEXT;",
        "ALTER TABLE goals ADD COLUMN heartbeat_enabled INTEGER DEFAULT 0;",
        "ALTER TABLE goals ADD COLUMN resume_policy TEXT DEFAULT 'manual';",
        "ALTER TABLE goals ADD COLUMN retry_policy TEXT DEFAULT '{\"max_retries\": 3, \"retry_count\": 0}';",
        "ALTER TABLE goals ADD COLUMN owner_session_id TEXT;",
    )
))

MIGRATIONS.append(Migration(
    version=11,
    name="prime_agent_hardening_v11",
    statements=(
        "ALTER TABLE goals ADD COLUMN lease_id TEXT;",
        "ALTER TABLE goals ADD COLUMN lease_expires_at REAL;",
        "ALTER TABLE goals ADD COLUMN max_cost REAL DEFAULT 0.0;",
        "ALTER TABLE goals ADD COLUMN max_failures INTEGER DEFAULT 5;",
        "ALTER TABLE goals ADD COLUMN max_retries INTEGER DEFAULT 3;",
        "ALTER TABLE goals ADD COLUMN max_children INTEGER DEFAULT 5;",
        "ALTER TABLE goals ADD COLUMN failures_used INTEGER DEFAULT 0;",
        "ALTER TABLE goals ADD COLUMN retries_used INTEGER DEFAULT 0;",
        "ALTER TABLE child_sessions ADD COLUMN current_task_id TEXT;",
        "ALTER TABLE child_sessions ADD COLUMN task_started_at REAL;",
        "ALTER TABLE child_sessions ADD COLUMN capabilities_json TEXT DEFAULT '[]';",
        "ALTER TABLE child_sessions ADD COLUMN context_summary TEXT DEFAULT '';",
        "ALTER TABLE child_messages ADD COLUMN correlation_id TEXT;",
        "ALTER TABLE child_messages ADD COLUMN reply_to TEXT;",
        "ALTER TABLE child_messages ADD COLUMN trace_id TEXT;",
        """
        CREATE INDEX IF NOT EXISTS idx_goals_due_lease
        ON goals(state, next_run_at, lease_expires_at);
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_child_messages_correlation
        ON child_messages(correlation_id);
        """,
    )
))


MIGRATIONS.append(Migration(
    version=12,
    name="prime_architecture_security_and_scheduler_v12",
    statements=(
        "ALTER TABLE pending_actions ADD COLUMN security_context_json TEXT DEFAULT '{}';",
        "ALTER TABLE child_sessions ADD COLUMN runtime_conversation_id TEXT;",
        "ALTER TABLE goals ADD COLUMN lease_owner_id TEXT;",
        "ALTER TABLE goals ADD COLUMN lease_heartbeat_at REAL;",
        "ALTER TABLE goals ADD COLUMN cost_used REAL DEFAULT 0.0;",
        "ALTER TABLE goals ADD COLUMN children_used INTEGER DEFAULT 0;",
        "ALTER TABLE goals ADD COLUMN capabilities_json TEXT DEFAULT '[]';",
        "CREATE INDEX IF NOT EXISTS idx_goals_lease_owner ON goals(lease_owner_id, lease_expires_at);",
        "CREATE INDEX IF NOT EXISTS idx_child_runtime_conversation ON child_sessions(runtime_conversation_id);",
    )
))

MIGRATIONS.append(Migration(
    version=13,
    name="goal_fence_subject_and_child_context_v13",
    statements=(
        "ALTER TABLE child_sessions ADD COLUMN security_context_json TEXT DEFAULT '{}';",
    )
))

MIGRATIONS.append(Migration(
    version=14,
    name="kitt_native_subsystem_v14",
    statements=(
        'CREATE TABLE IF NOT EXISTS native_memory_vectors (\n    memory_id TEXT PRIMARY KEY,\n    workspace_id TEXT NOT NULL,\n    vector_json TEXT NOT NULL,\n    dimensions INTEGER NOT NULL,\n    encoder TEXT NOT NULL,\n    updated_at REAL NOT NULL\n);',
        'CREATE INDEX IF NOT EXISTS idx_native_memory_vectors_workspace ON native_memory_vectors(workspace_id);',
        "CREATE TABLE IF NOT EXISTS knowledge_concepts (\n    id TEXT PRIMARY KEY,\n    workspace_id TEXT NOT NULL,\n    name TEXT NOT NULL,\n    definition TEXT NOT NULL,\n    confidence REAL NOT NULL DEFAULT 0.5,\n    revision INTEGER NOT NULL DEFAULT 1,\n    labels_json TEXT NOT NULL DEFAULT '[]',\n    source_memory_ids_json TEXT NOT NULL DEFAULT '[]',\n    created_at REAL NOT NULL,\n    updated_at REAL NOT NULL,\n    UNIQUE(workspace_id, name)\n);",
        'CREATE INDEX IF NOT EXISTS idx_knowledge_concepts_workspace ON knowledge_concepts(workspace_id);',
        'CREATE TABLE IF NOT EXISTS knowledge_links (\n    id TEXT PRIMARY KEY,\n    workspace_id TEXT NOT NULL,\n    source_id TEXT NOT NULL,\n    target_id TEXT NOT NULL,\n    relation TEXT NOT NULL,\n    weight REAL NOT NULL DEFAULT 1.0,\n    created_at REAL NOT NULL,\n    UNIQUE(workspace_id, source_id, target_id, relation),\n    CHECK(source_id <> target_id),\n    FOREIGN KEY(source_id) REFERENCES knowledge_concepts(id) ON DELETE CASCADE,\n    FOREIGN KEY(target_id) REFERENCES knowledge_concepts(id) ON DELETE CASCADE\n);',
        'CREATE INDEX IF NOT EXISTS idx_knowledge_links_source ON knowledge_links(workspace_id, source_id);',
        'CREATE INDEX IF NOT EXISTS idx_knowledge_links_target ON knowledge_links(workspace_id, target_id);',
        "CREATE TABLE IF NOT EXISTS correction_memories (\n    id TEXT PRIMARY KEY,\n    workspace_id TEXT NOT NULL,\n    context TEXT NOT NULL,\n    predicted TEXT NOT NULL,\n    corrected TEXT NOT NULL,\n    reason TEXT,\n    source TEXT NOT NULL DEFAULT 'user',\n    applied_count INTEGER NOT NULL DEFAULT 0,\n    vector_json TEXT,\n    created_at REAL NOT NULL,\n    updated_at REAL NOT NULL\n);",
        'CREATE INDEX IF NOT EXISTS idx_correction_memories_workspace ON correction_memories(workspace_id);',
        "CREATE TABLE IF NOT EXISTS coordination_leases (\n    workspace_id TEXT NOT NULL,\n    resource_id TEXT NOT NULL,\n    owner_id TEXT NOT NULL,\n    mode TEXT NOT NULL CHECK(mode IN ('READ','WRITE')),\n    intent TEXT NOT NULL,\n    lease_token TEXT NOT NULL,\n    acquired_at REAL NOT NULL,\n    expires_at REAL NOT NULL,\n    PRIMARY KEY(workspace_id, resource_id, owner_id)\n);",
        'CREATE INDEX IF NOT EXISTS idx_coordination_leases_expiry ON coordination_leases(workspace_id, expires_at);',
        'CREATE TABLE IF NOT EXISTS child_worktrees (\n    child_id TEXT PRIMARY KEY,\n    workspace_id TEXT NOT NULL,\n    path TEXT NOT NULL,\n    branch TEXT NOT NULL,\n    base_ref TEXT NOT NULL,\n    state TEXT NOT NULL,\n    created_at REAL NOT NULL,\n    updated_at REAL NOT NULL,\n    last_error TEXT\n);',
    )
))

class MigrationRunner:
    def __init__(self, migrations: list[Migration] = None):
        if migrations is None:
            migrations = MIGRATIONS
        self.migrations = sorted(migrations, key=lambda m: m.version)

    def _ensure_schema_info(self, conn: sqlite3.Connection):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_info (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(schema_info)")}
        if "applied_at" not in columns:
            conn.execute("ALTER TABLE schema_info ADD COLUMN applied_at REAL")
            conn.execute("UPDATE schema_info SET applied_at = ? WHERE applied_at IS NULL", (time.time(),))
        conn.commit()

    def get_current_version(self, conn: sqlite3.Connection) -> int:
        self._ensure_schema_info(conn)
        cur = conn.cursor()
        cur.execute("SELECT MAX(version) FROM schema_info")
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else 0

    def migrate(self, connection: sqlite3.Connection) -> int:
        current_version = self.get_current_version(connection)
        applied = 0
        
        for migration in self.migrations:
            if migration.version > current_version:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    for stmt in migration.statements:
                        if stmt.strip():
                            try:
                                connection.execute(stmt)
                            except sqlite3.OperationalError as exc:
                                err_msg = str(exc).lower()
                                if "duplicate column name" in err_msg or "already exists" in err_msg:
                                    continue
                                raise
                    
                    connection.execute(
                        "INSERT INTO schema_info (version, applied_at) VALUES (?, ?)",
                        (migration.version, time.time())
                    )
                    connection.commit()
                    applied += 1
                except Exception as e:
                    connection.rollback()
                    raise RuntimeError(f"Migration {migration.version} ({migration.name}) failed: {e}")
                    
        return applied
