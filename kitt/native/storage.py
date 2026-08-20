from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable


NATIVE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS native_memory_vectors (
    memory_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    encoder TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_native_memory_vectors_workspace ON native_memory_vectors(workspace_id);

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
CREATE INDEX IF NOT EXISTS idx_knowledge_concepts_workspace ON knowledge_concepts(workspace_id);

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
CREATE INDEX IF NOT EXISTS idx_knowledge_links_source ON knowledge_links(workspace_id, source_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_links_target ON knowledge_links(workspace_id, target_id);

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
CREATE INDEX IF NOT EXISTS idx_correction_memories_workspace ON correction_memories(workspace_id);

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
CREATE INDEX IF NOT EXISTS idx_coordination_leases_expiry ON coordination_leases(workspace_id, expires_at);

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

RELATIONS = {
    "PART_OF", "DEPENDS_ON", "RELATED_TO", "CONTRADICTS", "REFINES",
    "ALTERNATIVE_TO", "CAUSED_BY", "INSTANCE_OF", "SUPERSEDES",
}


@dataclass(frozen=True)
class Concept:
    id: str
    name: str
    definition: str
    confidence: float
    revision: int
    labels: tuple[str, ...]
    source_memory_ids: tuple[str, ...]


class NativeStateRepository:
    def __init__(self, db: Any, workspace_id: str):
        self.db = db
        self.workspace_id = workspace_id
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.db.get_connection() as conn:
            conn.executescript(NATIVE_SCHEMA_SQL)

    def put_vector(self, memory_id: str, vector: list[float], encoder: str) -> None:
        if not memory_id or len(memory_id) > 256:
            raise ValueError("memory_id is invalid")
        if not encoder or len(encoder) > 256:
            raise ValueError("encoder is invalid")
        if not vector or len(vector) > 8192 or not all(math.isfinite(float(x)) for x in vector):
            raise ValueError("memory vector is invalid")
        vector = [float(x) for x in vector]
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO native_memory_vectors(memory_id, workspace_id, vector_json, dimensions, encoder, updated_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(memory_id) DO UPDATE SET
                   vector_json=excluded.vector_json, dimensions=excluded.dimensions,
                   encoder=excluded.encoder, updated_at=excluded.updated_at""",
                (memory_id, self.workspace_id, json.dumps(vector, separators=(",", ":")), len(vector), encoder, now),
            )

    def get_vectors(
        self, memory_ids: Iterable[str], *, encoder: str | None = None, dimensions: int | None = None
    ) -> dict[str, list[float]]:
        ids = list(dict.fromkeys(memory_ids))
        if not ids:
            return {}
        with self.db.get_connection() as conn:
            placeholders = ",".join("?" for _ in ids)
            query = (
                "SELECT memory_id, vector_json, dimensions, encoder "
                f"FROM native_memory_vectors WHERE workspace_id=? AND memory_id IN ({placeholders})"
            )
            rows = conn.execute(query, (self.workspace_id, *ids)).fetchall()
        result: dict[str, list[float]] = {}
        for row in rows:
            if encoder is not None and str(row[3]) != encoder:
                continue
            if dimensions is not None and int(row[2]) != int(dimensions):
                continue
            vector = list(map(float, json.loads(row[1])))
            if dimensions is not None and len(vector) != int(dimensions):
                continue
            result[str(row[0])] = vector
        return result

    def upsert_concept(self, name: str, definition: str, confidence: float = 0.6,
                       labels: Iterable[str] = (), source_memory_ids: Iterable[str] = ()) -> Concept:
        name = str(name).strip()
        definition = str(definition).strip()
        if not name or len(name) > 256:
            raise ValueError("concept name must be 1..256 characters")
        if not definition or len(definition) > 16_384:
            raise ValueError("concept definition must be 1..16384 characters")
        normalized_labels = sorted({str(v).strip()[:128] for v in labels if str(v).strip()})[:64]
        normalized_sources = sorted({str(v).strip() for v in source_memory_ids if str(v).strip()})[:256]
        now = time.time()
        confidence = float(confidence)
        if not math.isfinite(confidence):
            raise ValueError("concept confidence must be finite")
        confidence = min(1.0, max(0.0, confidence))
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT id, revision, confidence, labels_json, source_memory_ids_json FROM knowledge_concepts WHERE workspace_id=? AND name=?",
                (self.workspace_id, name),
            ).fetchone()
            if row:
                cid, revision, old_conf, old_labels, old_sources = row
                merged_labels = sorted(set(json.loads(old_labels)) | set(normalized_labels))[:64]
                merged_sources = sorted(set(json.loads(old_sources)) | set(normalized_sources))[:256]
                confidence = max(float(old_conf), confidence)
                revision = int(revision) + 1
                conn.execute(
                    """UPDATE knowledge_concepts SET definition=?, confidence=?, revision=?, labels_json=?,
                       source_memory_ids_json=?, updated_at=? WHERE id=?""",
                    (definition, confidence, revision, json.dumps(merged_labels), json.dumps(merged_sources), now, cid),
                )
            else:
                cid, revision = f"concept_{uuid.uuid4().hex[:16]}", 1
                merged_labels, merged_sources = normalized_labels, normalized_sources
                conn.execute(
                    """INSERT INTO knowledge_concepts(id,workspace_id,name,definition,confidence,revision,labels_json,
                       source_memory_ids_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (cid, self.workspace_id, name, definition, confidence, revision,
                     json.dumps(merged_labels), json.dumps(merged_sources), now, now),
                )
        return Concept(cid, name, definition, confidence, revision, tuple(merged_labels), tuple(merged_sources))

    def add_link(self, source_id: str, target_id: str, relation: str, weight: float = 1.0) -> str:
        relation = relation.upper()
        if relation not in RELATIONS:
            raise ValueError(f"unsupported relation: {relation}")
        if source_id == target_id:
            raise ValueError("self-links are not allowed")
        weight = float(weight)
        if not math.isfinite(weight):
            raise ValueError("link weight must be finite")
        link_id = f"link_{uuid.uuid4().hex[:16]}"
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM knowledge_concepts WHERE workspace_id=? AND id IN (?,?)",
                (self.workspace_id, source_id, target_id),
            ).fetchall()
            if {str(row[0]) for row in rows} != {source_id, target_id}:
                raise PermissionError("knowledge link endpoints must belong to the active workspace")
            cursor = conn.execute(
                """INSERT OR IGNORE INTO knowledge_links(id,workspace_id,source_id,target_id,relation,weight,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (link_id, self.workspace_id, source_id, target_id, relation, weight, time.time()),
            )
            if cursor.rowcount:
                return link_id
            row = conn.execute(
                """SELECT id FROM knowledge_links
                   WHERE workspace_id=? AND source_id=? AND target_id=? AND relation=?""",
                (self.workspace_id, source_id, target_id, relation),
            ).fetchone()
            if row:
                return str(row[0])
        raise RuntimeError("knowledge link insert failed")

    def search_concepts(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        words = [w.casefold() for w in query.split() if len(w) >= 2]
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT id,name,definition,confidence,revision,labels_json,source_memory_ids_json FROM knowledge_concepts WHERE workspace_id=?",
                (self.workspace_id,),
            ).fetchall()
        scored = []
        for row in rows:
            hay = f"{row[1]} {row[2]} {' '.join(json.loads(row[5]))}".casefold()
            lexical = sum(1 for w in words if w in hay)
            if lexical or not words:
                scored.append((lexical + float(row[3]), {
                    "id": row[0], "name": row[1], "definition": row[2], "confidence": row[3],
                    "revision": row[4], "labels": json.loads(row[5]), "source_memory_ids": json.loads(row[6]),
                }))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def add_correction(self, context: str, predicted: str, corrected: str, reason: str | None = None,
                       source: str = "user", vector: list[float] | None = None) -> str:
        context = str(context).strip()
        predicted = str(predicted).strip()
        corrected = str(corrected).strip()
        reason = None if reason is None else str(reason).strip()[:4096]
        source = str(source).strip()[:128] or "user"
        if not context or len(context) > 4096:
            raise ValueError("correction context must be 1..4096 characters")
        if not predicted or len(predicted) > 8192 or not corrected or len(corrected) > 8192:
            raise ValueError("predicted/corrected text must be 1..8192 characters")
        if vector is not None and (len(vector) > 8192 or not all(math.isfinite(float(x)) for x in vector)):
            raise ValueError("correction vector is invalid")
        cid = f"correction_{uuid.uuid4().hex[:16]}"
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO correction_memories(id,workspace_id,context,predicted,corrected,reason,source,
                   applied_count,vector_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, self.workspace_id, context, predicted, corrected, reason, source, 0,
                 json.dumps(vector) if vector is not None else None, now, now),
            )
        return cid

    def list_corrections(self) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT id,context,predicted,corrected,reason,source,applied_count,vector_json,created_at FROM correction_memories WHERE workspace_id=?",
                (self.workspace_id,),
            ).fetchall()
        return [{
            "id": r[0], "context": r[1], "predicted": r[2], "corrected": r[3], "reason": r[4],
            "source": r[5], "applied_count": r[6], "vector": json.loads(r[7]) if r[7] else None,
            "created_at": r[8],
        } for r in rows]

    def mark_correction_applied(self, correction_id: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE correction_memories SET applied_count=applied_count+1,updated_at=? WHERE id=? AND workspace_id=?",
                (time.time(), correction_id, self.workspace_id),
            )
