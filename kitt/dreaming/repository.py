"""Dreaming repository adapter for the single kitt-memory authority."""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, List, Optional

from kitt.dreaming.models import DreamRun, MemoryEvidence, MemoryRecord
from kitt.memory.shared_client import KittMemoryClient


def _enum(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return raw
    if "_" in raw:
        return raw.upper()
    out = []
    for index, ch in enumerate(raw):
        if ch.isupper() and index:
            out.append("_")
        out.append(ch.upper())
    return "".join(out)


class MemoryRepository:
    """Compatibility facade; all durable state lives in kitt-memoryd."""

    def __init__(self, db=None, client: KittMemoryClient | None = None, namespace: str | None = None):
        self.client = client or KittMemoryClient()
        self.namespace = namespace or ("agent-cli" if db is None else f"agent-cli-test-{id(db)}")

    @staticmethod
    def _memory(row: dict[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            id=str(row["id"]),
            workspace_id=str(row.get("workspace_id") or ""),
            kind=_enum(row.get("kind")),
            content=str(row.get("content") or ""),
            normalized_content=str(row.get("normalized_content") or row.get("content") or ""),
            status=_enum(row.get("status")),
            importance=float(row.get("importance", 0.5)),
            confidence=float(row.get("confidence", 1.0)),
            created_at=float(row.get("created_at", 0)),
            updated_at=float(row.get("updated_at", 0)),
            last_accessed_at=float(row["last_accessed_at"]) if row.get("last_accessed_at") is not None else None,
            access_count=int(row.get("access_count", 0)),
            valid_from=float(row["valid_from"]) if row.get("valid_from") is not None else None,
            valid_until=float(row["valid_until"]) if row.get("valid_until") is not None else None,
            supersedes_id=row.get("supersedes_id"),
            content_hash=str(row.get("content_hash") or ""),
            pinned=bool(row.get("pinned")),
            metadata_json=str(row.get("metadata_json") or "{}"),
        )

    @staticmethod
    def _dream(row: dict[str, Any] | None) -> DreamRun | None:
        if not isinstance(row, dict):
            return None
        return DreamRun(
            id=str(row["id"]), workspace_id=str(row["workspace_id"]),
            started_at=float(row.get("started_at", 0)),
            finished_at=float(row["finished_at"]) if row.get("finished_at") is not None else None,
            status=str(row.get("status") or ""), sessions_scanned=int(row.get("sessions_scanned", 0)),
            entries_scanned=int(row.get("entries_scanned", 0)), signals_found=int(row.get("signals_found", 0)),
            memories_added=int(row.get("memories_added", 0)), memories_merged=int(row.get("memories_merged", 0)),
            memories_superseded=int(row.get("memories_superseded", 0)), memories_archived=int(row.get("memories_archived", 0)),
            model=str(row.get("model") or ""), input_tokens=int(row.get("input_tokens", 0)),
            output_tokens=int(row.get("output_tokens", 0)), failure_reason=row.get("failure_reason"),
            dry_run=bool(row.get("dry_run")),
        )

    def get_all_memories(self, workspace_id: str, status: Optional[str] = None) -> List[MemoryRecord]:
        args: dict[str, Any] = {"namespace": self.namespace, "workspace_id": workspace_id, "limit": 2048}
        if status:
            args["status"] = status
        body = self.client.manage("list", args)
        return [self._memory(row) for row in body.get("records", []) if isinstance(row, dict)]

    def get_active_memories(self, workspace_id: str) -> List[MemoryRecord]:
        return self.get_all_memories(workspace_id, "ACTIVE")

    def get_memory(self, memory_id: str) -> Optional[MemoryRecord]:
        row = self.client.manage("get", {"id": memory_id}).get("record")
        return self._memory(row) if isinstance(row, dict) else None

    def get_memory_by_content_hash(self, workspace_id: str, content_hash: str) -> Optional[MemoryRecord]:
        return next((row for row in self.get_all_memories(workspace_id) if row.content_hash == content_hash), None)

    def get_evidence_for_memory(self, memory_id: str) -> list[MemoryEvidence]:
        del memory_id
        return []

    def get_last_dream_run(self, workspace_id: str) -> Optional[DreamRun]:
        return self._dream(self.client.manage("dream.last", {"workspace_id": workspace_id}).get("run"))

    @staticmethod
    def _wire_run(run: DreamRun) -> dict[str, Any]:
        return {
            "id": run.id, "workspace_id": run.workspace_id, "started_at": int(run.started_at),
            "finished_at": int(run.finished_at) if run.finished_at is not None else None,
            "status": run.status, "sessions_scanned": run.sessions_scanned, "entries_scanned": run.entries_scanned,
            "signals_found": run.signals_found, "memories_added": run.memories_added, "memories_merged": run.memories_merged,
            "memories_superseded": run.memories_superseded, "memories_archived": run.memories_archived,
            "model": run.model, "input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
            "failure_reason": run.failure_reason, "dry_run": run.dry_run,
        }

    @staticmethod
    def _wire_memory(mem: MemoryRecord) -> dict[str, Any]:
        return {
            "id": mem.id, "namespace": self.namespace, "workspace_id": mem.workspace_id, "kind": mem.kind,
            "content": mem.content, "normalized_content": mem.normalized_content, "status": mem.status,
            "sensitivity": "private", "scope": "workspace", "scope_key": None,
            "importance": mem.importance, "confidence": mem.confidence,
            "created_at": int(mem.created_at), "updated_at": int(mem.updated_at),
            "last_accessed_at": int(mem.last_accessed_at) if mem.last_accessed_at is not None else None,
            "access_count": mem.access_count, "valid_from": int(mem.valid_from) if mem.valid_from is not None else None,
            "valid_until": int(mem.valid_until) if mem.valid_until is not None else None,
            "supersedes_id": mem.supersedes_id, "content_hash": mem.content_hash, "pinned": mem.pinned,
            "metadata_json": mem.metadata_json,
        }

    @staticmethod
    def _wire_source(ev: MemoryEvidence) -> dict[str, Any]:
        source_id = ev.session_entry_id or ev.conversation_id or ev.id
        uri = f"kitt://session/{ev.conversation_id}" if ev.conversation_id else None
        return {
            "id": ev.id or f"src_{uuid.uuid4().hex}", "memory_id": ev.memory_id,
            "source_kind": ev.source_kind, "source_id": source_id, "source_uri": uri,
            "source_digest": None, "relationship": "evidence", "source_revision": None,
            "observed_at": int(ev.created_at), "valid_from": None, "valid_until": None,
        }

    def record_dream_run(self, run: DreamRun) -> None:
        self.client.manage("dream.record", {"run": self._wire_run(run)})

    def commit_dream(self, workspace_id: str, dream_run: DreamRun, new_memories: List[MemoryRecord],
                     updated_memories: List[MemoryRecord], new_evidence: List[MemoryEvidence]) -> None:
        del workspace_id
        self.client.manage("dream.commit", {
            "run": self._wire_run(dream_run),
            "new_memories": [self._wire_memory(mem) for mem in new_memories],
            "updated_memories": [self._wire_memory(mem) for mem in updated_memories],
            "sources": [self._wire_source(ev) for ev in new_evidence],
        })

    def pin_memory(self, memory_id: str, pinned: bool = True) -> bool:
        return bool(self.client.manage("pin", {"id": memory_id, "pinned": pinned}).get("changed"))

    def set_memory_status(self, memory_id: str, status: str) -> bool:
        return bool(self.client.manage("set_status", {"id": memory_id, "status": status}).get("changed"))

    def touch_memory_access(self, memory_ids: list[str]) -> None:
        self.client.manage("touch", {"ids": list(memory_ids)})

    def archive_active_memories(self, workspace_id: str) -> List[MemoryRecord]:
        body = self.client.manage("archive_workspace", {"namespace": self.namespace, "workspace_id": workspace_id})
        return [self._memory(row) for row in body.get("records", []) if isinstance(row, dict)]

    def add_direct_memory(self, workspace_id: str, content: str, kind: str = "PROJECT_RULE",
                          pinned: bool = True, source_kind: str = "command_remember") -> MemoryRecord:
        del source_kind
        memory_id = self.client.remember(
            workspace_id,
            content,
            kind=kind,
            pinned=pinned,
            namespace=self.namespace,
        )
        record = self.get_memory(memory_id)
        if record is None:
            raise RuntimeError("kitt-memoryd remembered a record that cannot be read back")
        return record

    def rebuild_materialized_view(self, workspace_id: str, root_dir: Optional[Path] = None) -> str:
        active = self.get_active_memories(workspace_id)
        titles = {
            "PROJECT_RULE": "Project Rules", "ARCHITECTURE_DECISION": "Architecture Decisions",
            "USER_PREFERENCE": "User Preferences", "WORKING_PATTERN": "Working Patterns",
            "TECHNICAL_FACT": "Technical Facts", "FAILED_APPROACH": "Known Failures",
            "OPEN_ISSUE": "Open Issues", "PROJECT_STATE": "Project State",
        }
        grouped: dict[str, list[str]] = {key: [] for key in titles}
        for mem in active:
            if mem.kind in grouped:
                grouped[mem.kind].append(f"- {mem.content}{' 📌' if mem.pinned else ''}")
        lines = ["# K.I.T.T. Memory (Materialized Projection)", ""]
        for kind, title in titles.items():
            if grouped[kind]:
                lines.extend([f"## {title}", *grouped[kind], ""])
        content = "\n".join(lines).rstrip() + "\n"
        if root_dir:
            target = Path(root_dir) / ".kitt" / "memory" / "MEMORY.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
            try:
                tmp.write_text(content, encoding="utf-8")
                os.replace(tmp, target)
            finally:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
        return content
