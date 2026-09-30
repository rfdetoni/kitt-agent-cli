from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict
from typing import Any

from kitt.security.workspace_fs import WorkspaceFileSystem
from kitt_protocol import WorkspaceSnapshot


class WorkspaceSnapshotService:
    """Exact pre-mutation file snapshots backed by ArtifactStore + EventLedger."""

    def __init__(
        self,
        root,
        *,
        workspace_id: str,
        artifact_store: Any,
        ledger: Any = None,
    ) -> None:
        self.fs = WorkspaceFileSystem(root)
        self.workspace_id = str(workspace_id or "")
        self.artifacts = artifact_store
        self.ledger = ledger
        self._records: dict[str, dict[str, Any]] = {}
        self._last_by_turn: dict[str, str] = {}

    @staticmethod
    def _digest(entries: list[dict[str, Any]]) -> str:
        raw = json.dumps(
            entries,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def capture(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        paths: list[str],
    ) -> WorkspaceSnapshot:
        normalized = []
        for path in paths:
            rel = self.fs.relative(path)
            if rel == "." or rel in normalized:
                continue
            normalized.append(rel)
        if not normalized:
            raise ValueError("workspace snapshot requires concrete file paths")

        snapshot_id = f"wsnap_{uuid.uuid4().hex}"
        entries: list[dict[str, Any]] = []
        for rel in normalized:
            if not self.fs.exists_regular(rel):
                entries.append(
                    {
                        "path": rel,
                        "existed": False,
                        "artifact_id": None,
                        "sha256": None,
                    }
                )
                continue
            data = self.fs.read(rel)
            artifact = self.artifacts.put(
                self.workspace_id,
                data.content,
                "WORKSPACE_SNAPSHOT_FILE",
                f"Pre-mutation snapshot of {rel}",
                conversation_id=conversation_id or None,
                turn_id=turn_id or None,
                sensitivity="PRIVATE",
                metadata={
                    "snapshot_id": snapshot_id,
                    "path": rel,
                    "sha256": data.sha256,
                    "recovery": "EXACT",
                },
            )
            entries.append(
                {
                    "path": rel,
                    "existed": True,
                    "artifact_id": artifact.id,
                    "sha256": data.sha256,
                }
            )

        digest = self._digest(entries)
        parent = self._last_by_turn.get(turn_id)
        snapshot = WorkspaceSnapshot(
            snapshot_id=snapshot_id,
            turn_id=turn_id,
            created_at=int(time.time() * 1000),
            changed_paths=tuple(normalized),
            digest=digest,
            parent_snapshot_id=parent,
        )
        record = {
            "snapshot": asdict(snapshot),
            "entries": entries,
            "conversation_id": conversation_id,
        }
        self._records[snapshot_id] = record
        self._last_by_turn[turn_id] = snapshot_id
        if self.ledger is not None and conversation_id:
            self.ledger.append_event(
                conversation_id,
                "WorkspaceSnapshotCreated",
                record,
                turn_id=turn_id,
                source="workspace-snapshot",
                durability="SYNC",
                replayable=True,
            )
        return snapshot

    def _load_record(
        self,
        snapshot_id: str,
        conversation_id: str,
        turn_id: str,
    ) -> dict[str, Any]:
        cached = self._records.get(snapshot_id)
        if cached is not None:
            return cached
        if self.ledger is None:
            raise KeyError(snapshot_id)
        for event in reversed(
            self.ledger.events(
                conversation_id,
                turn_id=turn_id,
                limit=10000,
            )
        ):
            if event.event_type != "WorkspaceSnapshotCreated":
                continue
            payload = dict(event.payload)
            snap = payload.get("snapshot")
            if (
                isinstance(snap, dict)
                and str(snap.get("snapshot_id") or "") == snapshot_id
            ):
                self._records[snapshot_id] = payload
                return payload
        raise KeyError(snapshot_id)

    def restore(
        self,
        snapshot_id: str,
        *,
        conversation_id: str,
        turn_id: str,
    ) -> list[str]:
        record = self._load_record(snapshot_id, conversation_id, turn_id)
        entries = record.get("entries")
        if not isinstance(entries, list):
            raise ValueError("invalid workspace snapshot record")

        restored: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            rel = self.fs.relative(str(item.get("path") or ""))
            existed = bool(item.get("existed"))
            if existed:
                artifact_id = str(item.get("artifact_id") or "")
                if not artifact_id:
                    raise ValueError(f"snapshot entry has no artifact for {rel}")
                raw = self.artifacts.read(artifact_id)
                expected = str(item.get("sha256") or "")
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError(f"snapshot artifact digest mismatch for {rel}")
                self.fs.atomic_write(rel, raw)
                restored.append(rel)
            elif self.fs.exists_regular(rel):
                self.fs.unlink(rel)
                restored.append(rel)

        if self.ledger is not None and conversation_id:
            self.ledger.append_event(
                conversation_id,
                "WorkspaceSnapshotRestored",
                {
                    "snapshot_id": snapshot_id,
                    "restored_paths": restored,
                },
                turn_id=turn_id,
                source="workspace-snapshot",
                durability="SYNC",
                replayable=True,
            )
        return restored


__all__ = ["WorkspaceSnapshotService"]
