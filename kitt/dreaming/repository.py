"""SQLite repository for durable memories, evidence, and dream execution logs."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from kitt.dreaming.models import (
    MemoryRecord,
    MemoryEvidence,
    MemoryKind,
    MemoryStatus,
    DreamRun,
    DreamPlan,
    DreamSnapshot,
)
from kitt.history.database import HistoryDatabase


class MemoryRepository:
    """Manages SQLite persistence for canonical memories, evidence provenance, and dream runs."""

    def __init__(self, db: HistoryDatabase):
        self.db = db

    def get_active_memories(self, workspace_id: str) -> List[MemoryRecord]:
        return self.get_all_memories(workspace_id, status="ACTIVE")

    def get_all_memories(self, workspace_id: str, status: Optional[str] = None) -> List[MemoryRecord]:
        query = "SELECT * FROM memories WHERE workspace_id = ?"
        params: List[Any] = [workspace_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY pinned DESC, importance DESC, created_at DESC"

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            return [self._row_to_memory(r) for r in rows]

    def get_memory(self, memory_id: str) -> Optional[MemoryRecord]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            row = cursor.fetchone()
            return self._row_to_memory(row) if row else None

    def get_memory_by_content_hash(self, workspace_id: str, content_hash: str) -> Optional[MemoryRecord]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM memories WHERE workspace_id = ? AND content_hash = ? AND status != 'ARCHIVED'",
                (workspace_id, content_hash)
            )
            row = cursor.fetchone()
            return self._row_to_memory(row) if row else None

    def get_evidence_for_memory(self, memory_id: str) -> List[MemoryEvidence]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM memory_evidence WHERE memory_id = ? ORDER BY created_at ASC", (memory_id,))
            rows = cursor.fetchall()
            return [self._row_to_evidence(r) for r in rows]

    def get_last_dream_run(self, workspace_id: str) -> Optional[DreamRun]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM dream_runs WHERE workspace_id = ? AND status = 'COMPLETED' AND dry_run = 0 ORDER BY finished_at DESC LIMIT 1",
                (workspace_id,)
            )
            row = cursor.fetchone()
            return self._row_to_dream_run(row) if row else None

    def record_dream_run(self, run: DreamRun) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO dream_runs (
                    id, workspace_id, started_at, finished_at, status,
                    sessions_scanned, entries_scanned, signals_found,
                    memories_added, memories_merged, memories_superseded, memories_archived,
                    model, input_tokens, output_tokens, failure_reason, dry_run
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id, run.workspace_id, run.started_at, run.finished_at, run.status,
                    run.sessions_scanned, run.entries_scanned, run.signals_found,
                    run.memories_added, run.memories_merged, run.memories_superseded, run.memories_archived,
                    run.model, run.input_tokens, run.output_tokens, run.failure_reason, 1 if run.dry_run else 0
                )
            )

    def commit_dream(
        self,
        workspace_id: str,
        dream_run: DreamRun,
        new_memories: List[MemoryRecord],
        updated_memories: List[MemoryRecord],
        new_evidence: List[MemoryEvidence],
    ) -> None:
        """Atomic transactional commit for Dreaming Mode plan execution."""
        with self.db.get_connection() as conn:
            # 1. Update existing/superseded memories
            for mem in updated_memories:
                conn.execute(
                    """
                    UPDATE memories SET
                        kind = ?, content = ?, normalized_content = ?, status = ?,
                        importance = ?, confidence = ?, updated_at = ?,
                        last_accessed_at = ?, access_count = ?, valid_from = ?,
                        valid_until = ?, supersedes_id = ?, content_hash = ?,
                        pinned = ?, metadata_json = ?
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (
                        mem.kind, mem.content, mem.normalized_content, mem.status,
                        mem.importance, mem.confidence, mem.updated_at,
                        mem.last_accessed_at, mem.access_count, mem.valid_from,
                        mem.valid_until, mem.supersedes_id, mem.content_hash,
                        1 if mem.pinned else 0, mem.metadata_json,
                        mem.id, workspace_id
                    )
                )

            # 2. Insert new memories
            for mem in new_memories:
                conn.execute(
                    """
                    INSERT INTO memories (
                        id, workspace_id, kind, content, normalized_content, status,
                        importance, confidence, created_at, updated_at,
                        last_accessed_at, access_count, valid_from, valid_until,
                        supersedes_id, content_hash, pinned, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mem.id, workspace_id, mem.kind, mem.content, mem.normalized_content, mem.status,
                        mem.importance, mem.confidence, mem.created_at, mem.updated_at,
                        mem.last_accessed_at, mem.access_count, mem.valid_from, mem.valid_until,
                        mem.supersedes_id, mem.content_hash, 1 if mem.pinned else 0, mem.metadata_json
                    )
                )

            # 3. Insert evidence records
            for ev in new_evidence:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO memory_evidence (
                        id, memory_id, workspace_id, session_entry_id,
                        conversation_id, source_kind, evidence_text, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev.id, ev.memory_id, workspace_id, ev.session_entry_id,
                        ev.conversation_id, ev.source_kind, ev.evidence_text, ev.created_at
                    )
                )

            # 4. Record the completed dream run
            conn.execute(
                """
                INSERT OR REPLACE INTO dream_runs (
                    id, workspace_id, started_at, finished_at, status,
                    sessions_scanned, entries_scanned, signals_found,
                    memories_added, memories_merged, memories_superseded, memories_archived,
                    model, input_tokens, output_tokens, failure_reason, dry_run
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dream_run.id, workspace_id, dream_run.started_at, dream_run.finished_at, dream_run.status,
                    dream_run.sessions_scanned, dream_run.entries_scanned, dream_run.signals_found,
                    dream_run.memories_added, dream_run.memories_merged, dream_run.memories_superseded, dream_run.memories_archived,
                    dream_run.model, dream_run.input_tokens, dream_run.output_tokens, dream_run.failure_reason, 1 if dream_run.dry_run else 0
                )
            )

    def touch_memory_access(self, memory_ids: List[str]) -> None:
        if not memory_ids:
            return
        now = time.time()
        with self.db.get_connection() as conn:
            placeholders = ",".join("?" for _ in memory_ids)
            conn.execute(
                f"""
                UPDATE memories SET
                    last_accessed_at = ?,
                    access_count = access_count + 1
                WHERE id IN ({placeholders})
                """,
                (now, *memory_ids)
            )

    def pin_memory(self, memory_id: str, pinned: bool = True) -> bool:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE memories SET pinned = ?, updated_at = ? WHERE id = ?",
                (1 if pinned else 0, time.time(), memory_id)
            )
            return cursor.rowcount > 0

    def set_memory_status(self, memory_id: str, status: MemoryStatus) -> bool:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
                (status, time.time(), memory_id)
            )
            return cursor.rowcount > 0

    def add_direct_memory(
        self,
        workspace_id: str,
        content: str,
        kind: MemoryKind = "PROJECT_RULE",
        pinned: bool = True,
        source_kind: str = "command_remember"
    ) -> MemoryRecord:
        import uuid
        now = time.time()
        mem_id = f"mem_{uuid.uuid4().hex[:12]}"
        normalized = content.strip()
        mem = MemoryRecord(
            id=mem_id,
            workspace_id=workspace_id,
            kind=kind,
            content=content,
            normalized_content=normalized,
            status="ACTIVE",
            importance=0.9 if pinned else 0.5,
            confidence=1.0,
            created_at=now,
            updated_at=now,
            pinned=pinned,
        )
        ev = MemoryEvidence(
            id=f"ev_{uuid.uuid4().hex[:12]}",
            memory_id=mem_id,
            workspace_id=workspace_id,
            session_entry_id=None,
            conversation_id=None,
            source_kind=source_kind,
            evidence_text=content,
            created_at=now,
        )
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    id, workspace_id, kind, content, normalized_content, status,
                    importance, confidence, created_at, updated_at,
                    last_accessed_at, access_count, valid_from, valid_until,
                    supersedes_id, content_hash, pinned, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mem.id, mem.workspace_id, mem.kind, mem.content, mem.normalized_content, mem.status,
                    mem.importance, mem.confidence, mem.created_at, mem.updated_at,
                    mem.last_accessed_at, mem.access_count, mem.valid_from, mem.valid_until,
                    mem.supersedes_id, mem.content_hash, 1 if mem.pinned else 0, mem.metadata_json
                )
            )
            conn.execute(
                """
                INSERT INTO memory_evidence (
                    id, memory_id, workspace_id, session_entry_id,
                    conversation_id, source_kind, evidence_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev.id, ev.memory_id, ev.workspace_id, ev.session_entry_id,
                    ev.conversation_id, ev.source_kind, ev.evidence_text, ev.created_at
                )
            )
        return mem

    def rebuild_materialized_view(self, workspace_id: str, root_dir: Optional[Path] = None) -> str:
        """Reconstructs the human-readable .kitt/memory/MEMORY.md projection from SQLite."""
        active = self.get_active_memories(workspace_id)
        sections: Dict[str, List[str]] = {
            "PROJECT_RULE": [],
            "ARCHITECTURE_DECISION": [],
            "USER_PREFERENCE": [],
            "WORKING_PATTERN": [],
            "TECHNICAL_FACT": [],
            "FAILED_APPROACH": [],
            "OPEN_ISSUE": [],
            "PROJECT_STATE": [],
        }

        titles = {
            "PROJECT_RULE": "Project Rules",
            "ARCHITECTURE_DECISION": "Architecture Decisions",
            "USER_PREFERENCE": "User Preferences",
            "WORKING_PATTERN": "Working Patterns",
            "TECHNICAL_FACT": "Technical Facts",
            "FAILED_APPROACH": "Known Failures",
            "OPEN_ISSUE": "Open Issues",
            "PROJECT_STATE": "Project State",
        }

        for mem in active:
            if mem.kind in sections:
                pin_mark = " 📌" if mem.pinned else ""
                sections[mem.kind].append(f"- {mem.content}{pin_mark}")

        lines = ["# K.I.T.T. Memory (Materialized Projection)\n"]
        for kind, items in sections.items():
            if items:
                lines.append(f"## {titles[kind]}")
                lines.extend(items)
                lines.append("")

        content = "\n".join(lines).strip() + "\n"

        if root_dir:
            target = Path(root_dir) / ".kitt" / "memory" / "MEMORY.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        return content

    def _row_to_memory(self, row: Any) -> MemoryRecord:
        d = dict(row)
        return MemoryRecord(
            id=d["id"],
            workspace_id=d["workspace_id"],
            kind=d["kind"],
            content=d["content"],
            normalized_content=d["normalized_content"],
            status=d["status"],
            importance=float(d["importance"]),
            confidence=float(d["confidence"]),
            created_at=float(d["created_at"]),
            updated_at=float(d["updated_at"]),
            last_accessed_at=float(d["last_accessed_at"]) if d.get("last_accessed_at") is not None else None,
            access_count=int(d.get("access_count", 0)),
            valid_from=float(d["valid_from"]) if d.get("valid_from") is not None else None,
            valid_until=float(d["valid_until"]) if d.get("valid_until") is not None else None,
            supersedes_id=d.get("supersedes_id"),
            content_hash=d.get("content_hash", ""),
            pinned=bool(d.get("pinned", 0)),
            metadata_json=d.get("metadata_json", "{}"),
        )

    def _row_to_evidence(self, row: Any) -> MemoryEvidence:
        d = dict(row)
        return MemoryEvidence(
            id=d["id"],
            memory_id=d["memory_id"],
            workspace_id=d["workspace_id"],
            session_entry_id=d.get("session_entry_id"),
            conversation_id=d.get("conversation_id"),
            source_kind=d["source_kind"],
            evidence_text=d["evidence_text"],
            created_at=float(d["created_at"]),
        )

    def _row_to_dream_run(self, row: Any) -> DreamRun:
        d = dict(row)
        return DreamRun(
            id=d["id"],
            workspace_id=d["workspace_id"],
            started_at=float(d["started_at"]),
            finished_at=float(d["finished_at"]) if d.get("finished_at") is not None else None,
            status=d["status"],
            sessions_scanned=int(d.get("sessions_scanned", 0)),
            entries_scanned=int(d.get("entries_scanned", 0)),
            signals_found=int(d.get("signals_found", 0)),
            memories_added=int(d.get("memories_added", 0)),
            memories_merged=int(d.get("memories_merged", 0)),
            memories_superseded=int(d.get("memories_superseded", 0)),
            memories_archived=int(d.get("memories_archived", 0)),
            model=d.get("model", ""),
            input_tokens=int(d.get("input_tokens", 0)),
            output_tokens=int(d.get("output_tokens", 0)),
            failure_reason=d.get("failure_reason"),
            dry_run=bool(d.get("dry_run", 0)),
        )
