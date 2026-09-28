from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from kitt.native.storage import NativeStateRepository


class Db:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")

    @contextmanager
    def get_connection(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


def test_native_state_contains_coordination_only():
    db = Db()
    NativeStateRepository(db, "ws")
    tables = {
        row[0]
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "coordination_leases" in tables
    assert "coordination_wait_queue" in tables
    assert "child_worktrees" in tables
    assert "native_memory_vectors" not in tables
    assert "knowledge_concepts" not in tables
    assert "knowledge_links" not in tables
    assert "correction_memories" not in tables
