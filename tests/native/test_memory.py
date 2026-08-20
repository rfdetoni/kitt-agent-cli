from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

from kitt.native.memory import HybridMemoryService
from kitt.native.storage import NativeStateRepository


class Db:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def get_connection(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback(); raise


@dataclass
class Memory:
    id: str
    content: str
    kind: str = "TECHNICAL_FACT"
    normalized_content: str = ""
    importance: float = 0.7
    confidence: float = 0.9
    access_count: int = 0
    pinned: bool = False
    content_hash: str = "abc123456789"

    def __post_init__(self):
        if not self.normalized_content:
            self.normalized_content = self.content


class MemoryRepo:
    def __init__(self):
        self.rows = [
            Memory("m1", "Provider authentication uses OAuth before model selection", "ARCHITECTURE_DECISION", pinned=True),
            Memory("m2", "Tests are executed with pytest", "PROJECT_RULE"),
        ]
        self.touched = []

    def get_active_memories(self, workspace_id):
        return self.rows

    def touch_memory_access(self, ids):
        self.touched.extend(ids)


class Base:
    def get_memory_context(self, prompt="", max_tokens=400):
        return "base"
    def get_relevant_memories(self, prompt):
        return []


def test_hybrid_memory_and_correction():
    db = Db(); native = NativeStateRepository(db, "ws")
    repo = MemoryRepo(); service = HybridMemoryService(Base(), repo, native)
    service.remember_correction("provider auth", "skip auth", "authenticate before model selection", "provider requires credentials")
    rows = service.query("provider authentication model", limit=4)
    assert rows
    assert any(row["source"] == "memory" for row in rows)
    assert any(row["source"] == "correction" for row in rows)
    assert repo.touched


def test_knowledge_graph_roundtrip():
    db = Db(); native = NativeStateRepository(db, "ws")
    a = native.upsert_concept("ProviderRegistry", "Discovers provider adapters", 0.8, ["domain:providers"], ["m1"])
    b = native.upsert_concept("OAuthService", "Authenticates providers", 0.9, ["domain:auth"], ["m2"])
    native.add_link(a.id, b.id, "DEPENDS_ON")
    found = native.search_concepts("provider adapters", 5)
    assert found[0]["name"] == "ProviderRegistry"


def test_knowledge_link_insert_is_idempotent_and_returns_persisted_id():
    db = Db(); native = NativeStateRepository(db, "ws")
    a = native.upsert_concept("ProviderRegistry", "Discovers provider adapters", 0.8)
    b = native.upsert_concept("OAuthService", "Authenticates providers", 0.9)
    first = native.add_link(a.id, b.id, "DEPENDS_ON")
    second = native.add_link(a.id, b.id, "DEPENDS_ON")
    assert second == first
    row = db.conn.execute(
        "SELECT id, COUNT(*) FROM knowledge_links WHERE workspace_id=? AND source_id=? AND target_id=? AND relation=?",
        ("ws", a.id, b.id, "DEPENDS_ON"),
    ).fetchone()
    assert row == (first, 1)
