from __future__ import annotations

from typing import Any


NATIVE_SCHEMA_SQL = """
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
CREATE INDEX IF NOT EXISTS idx_coordination_leases_expiry
ON coordination_leases(workspace_id, expires_at);

CREATE TABLE IF NOT EXISTS coordination_wait_queue (
    workspace_id TEXT NOT NULL,
    ticket_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    resources_json TEXT NOT NULL,
    intent TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY(workspace_id, ticket_id)
);
CREATE INDEX IF NOT EXISTS idx_coordination_wait_queue_order
ON coordination_wait_queue(workspace_id, created_at, ticket_id);

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
"""


class NativeStateRepository:
    """Agent-owned coordination state only.

    Durable semantic memory, corrections and knowledge graphs belong to
    kitt-memory and must never be recreated in the Agent history database.
    """

    def __init__(self, db: Any, workspace_id: str):
        self.db = db
        self.workspace_id = workspace_id
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.db.get_connection() as conn:
            conn.executescript(NATIVE_SCHEMA_SQL)
