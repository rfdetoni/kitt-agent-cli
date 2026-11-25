from __future__ import annotations

import hashlib
import json
import time
import uuid
import base64
from dataclasses import asdict
from typing import Any
from pathlib import Path

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
            if self.fs.is_safe_directory(rel):
                manifest = self._tree_manifest(
                    rel, snapshot_id=snapshot_id,
                    conversation_id=conversation_id, turn_id=turn_id,
                )
                raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                artifact = self.artifacts.put(
                    self.workspace_id, raw, "WORKSPACE_SNAPSHOT_DIRECTORY",
                    f"Pre-mutation snapshot of {rel}", conversation_id=conversation_id or None,
                    turn_id=turn_id or None, sensitivity="PRIVATE",
                    metadata={"snapshot_id": snapshot_id, "path": rel, "format": 2},
                )
                entries.append({
                    "path": rel, "existed": True, "kind": "directory",
                    "artifact_id": artifact.id,
                    "sha256": self._manifest_digest(manifest),
                })
                continue
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

    def _tree(self, rel: str) -> bytes:
        """Bounded, deterministic manifest; links and special files fail closed."""
        import os
        import stat
        manifest = []
        size = 0
        base = self.fs.absolute_lexical(rel)
        for current, dirs, files in os.walk(base, followlinks=False):
            for name in sorted(dirs + files):
                path = Path(current) / name
                relative = path.relative_to(base).as_posix()
                workspace_path = path.relative_to(self.fs.root).as_posix()
                st = path.lstat()
                if stat.S_ISLNK(st.st_mode) or self.fs._windows_reparse_point(st):
                    raise PermissionError("Snapshot refuses links/reparse points")
                if stat.S_ISDIR(st.st_mode):
                    item = {"path": relative, "directory": True}
                elif stat.S_ISREG(st.st_mode):
                    data = self.fs.read(workspace_path).content
                    size += len(data)
                    item = {"path": relative, "content": base64.b64encode(data).decode("ascii")}
                else:
                    raise PermissionError("Snapshot refuses special files")
                manifest.append(item)
                if len(manifest) > 1000 or size > 32 * 1024 * 1024:
                    raise ValueError("Directory snapshot exceeds recovery budget")
        return json.dumps(sorted(manifest, key=lambda item: item["path"]), sort_keys=True, separators=(",", ":")).encode()

    @classmethod
    def _manifest_digest(cls, manifest: list[dict[str, Any]]) -> str:
        # Storage identities are not content identities. Unchanged directory
        # contents must have equal digests across different snapshots.
        return "directory-v2:" + cls._digest([
            {key: value for key, value in entry.items() if key != "artifact_id"}
            for entry in manifest
        ])

    def _tree_manifest(
        self, rel: str, *, snapshot_id: str | None = None,
        conversation_id: str = "", turn_id: str = "",
    ) -> list[dict[str, Any]]:
        """Store file preimages independently, without a directory-sized buffer."""
        import os
        import stat

        base = self.fs.absolute_lexical(rel)
        manifest: list[dict[str, Any]] = []
        size = 0
        for current, dirs, files in os.walk(base, followlinks=False):
            dirs.sort()
            for name in sorted(dirs + files):
                if len(manifest) >= 1000:
                    raise ValueError("Directory snapshot exceeds recovery budget")
                path = Path(current) / name
                relative = path.relative_to(base).as_posix()
                workspace_path = path.relative_to(self.fs.root).as_posix()
                st = path.lstat()
                if stat.S_ISLNK(st.st_mode) or self.fs._windows_reparse_point(st):
                    raise PermissionError("Snapshot refuses links/reparse points")
                if stat.S_ISDIR(st.st_mode):
                    manifest.append({"path": relative, "directory": True})
                    continue
                if not stat.S_ISREG(st.st_mode):
                    raise PermissionError("Snapshot refuses special files")
                remaining = 32 * 1024 * 1024 - size
                if st.st_size > remaining:
                    raise ValueError("Directory snapshot exceeds recovery budget")
                data = self.fs.read(workspace_path)
                size += len(data.content)
                if size > 32 * 1024 * 1024:
                    raise ValueError("Directory snapshot exceeds recovery budget")
                item = {"path": relative, "sha256": data.sha256, "size": len(data.content)}
                if snapshot_id is not None:
                    artifact = self.artifacts.put(
                        self.workspace_id, data.content, "WORKSPACE_SNAPSHOT_FILE",
                        f"Pre-mutation snapshot of {workspace_path}",
                        conversation_id=conversation_id or None, turn_id=turn_id or None,
                        sensitivity="PRIVATE",
                        metadata={"snapshot_id": snapshot_id, "path": workspace_path,
                                  "sha256": data.sha256, "recovery": "EXACT"},
                    )
                    item["artifact_id"] = artifact.id
                manifest.append(item)
        return sorted(manifest, key=lambda item: item["path"])

    def _current_sha(self, rel: str, before_sha: str | None = None) -> str | None:
        if self.fs.is_safe_directory(rel):
            if before_sha and before_sha.startswith("directory:"):
                return "directory:" + hashlib.sha256(self._tree(rel)).hexdigest()
            return self._manifest_digest(self._tree_manifest(rel))
        return self.fs.read(rel).sha256 if self.fs.exists_regular(rel) else None

    def _restore_directory_v2(self, rel: str, raw: bytes, expected: str) -> None:
        manifest = json.loads(raw)
        if not isinstance(manifest, list) or self._manifest_digest(manifest) != expected:
            raise ValueError(f"snapshot artifact digest mismatch for {rel}")
        # Verify every child before any destructive operation, including
        # containment and content integrity. Never hold the complete tree in RAM.
        for entry in manifest:
            target = rel + "/" + entry["path"]
            if self.fs.relative(target) != target or not target.startswith(rel + "/"):
                raise ValueError("invalid snapshot path")
            if not entry.get("directory"):
                content = self.artifacts.read(entry["artifact_id"])
                if (len(content) != entry["size"]
                        or hashlib.sha256(content).hexdigest() != entry["sha256"]):
                    raise ValueError(f"snapshot artifact digest mismatch for {target}")
        from kitt.security.workspace_mutations import delete_path
        if self.fs.is_safe_directory(rel):
            delete_path(self.fs, rel, recursive=True)
        elif self.fs.exists_regular(rel):
            self.fs.unlink(rel)
        self.fs.create_directory(rel, parents=True, exist_ok=True)
        for entry in manifest:
            target = rel + "/" + entry["path"]
            if entry.get("directory"):
                self.fs.create_directory(target, parents=True, exist_ok=True)
            else:
                content = self.artifacts.read(entry["artifact_id"])
                if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                    raise ValueError(f"snapshot artifact digest mismatch for {target}")
                self.fs.atomic_write(target, content)

    def _load_record(
        self,
        snapshot_id: str,
        conversation_id: str,
        turn_id: str,
    ) -> dict[str, Any]:
        cached = self._records.get(snapshot_id)
        if cached is not None:
            if (cached.get("conversation_id") != conversation_id
                    or cached.get("snapshot", {}).get("turn_id") != turn_id):
                raise KeyError(snapshot_id)
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

    def diff(
        self,
        snapshot_id: str,
        *,
        conversation_id: str,
        turn_id: str,
        paths: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        record = self._load_record(snapshot_id, conversation_id, turn_id)
        entries = record.get("entries")
        if not isinstance(entries, list):
            raise ValueError("invalid workspace snapshot record")
        selected = None
        if paths is not None:
            selected = {self.fs.relative(path) for path in paths}
            known = {
                self.fs.relative(str(item.get("path") or ""))
                for item in entries
                if isinstance(item, dict)
            }
            unknown = sorted(selected - known)
            if unknown:
                raise ValueError(
                    "diff paths are not part of snapshot: " + ", ".join(unknown)
                )
        changes: list[dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            rel = self.fs.relative(str(item.get("path") or ""))
            if selected is not None and rel not in selected:
                continue
            existed = bool(item.get("existed"))
            before_sha = str(item.get("sha256") or "") or None
            current_sha = self._current_sha(rel, before_sha)
            current_exists = current_sha is not None
            if existed and not current_exists:
                status = "DELETED"
            elif not existed and current_exists:
                status = "ADDED"
            elif existed and current_exists and current_sha != before_sha:
                status = "MODIFIED"
            else:
                status = "UNCHANGED"
            changes.append(
                {
                    "path": rel,
                    "status": status,
                    "before_exists": existed,
                    "current_exists": current_exists,
                    "before_sha256": before_sha,
                    "current_sha256": current_sha,
                }
            )
        return changes

    def preview_restore(
        self,
        snapshot_id: str,
        *,
        conversation_id: str,
        turn_id: str,
        paths: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        changes = self.diff(
            snapshot_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            paths=paths,
        )
        selected = None
        if paths is not None:
            selected = {self.fs.relative(path) for path in paths}
        preview = []
        for change in changes:
            if selected is not None and change["path"] not in selected:
                continue
            if change["status"] == "UNCHANGED":
                action = "NOOP"
            elif change["before_exists"]:
                action = "RESTORE"
            else:
                action = "DELETE_CREATED_FILE"
            preview.append({**change, "restore_action": action})
        if selected is not None:
            known = {item["path"] for item in changes}
            unknown = sorted(selected - known)
            if unknown:
                raise ValueError(
                    "restore paths are not part of snapshot: " + ", ".join(unknown)
                )
        return preview

    def restore(
        self,
        snapshot_id: str,
        *,
        conversation_id: str,
        turn_id: str,
        paths: list[str] | None = None,
        expected_current: dict[str, str | None] | None = None,
    ) -> list[str]:
        record = self._load_record(snapshot_id, conversation_id, turn_id)
        entries = record.get("entries")
        if not isinstance(entries, list):
            raise ValueError("invalid workspace snapshot record")

        selected = None
        if paths is not None:
            selected = {self.fs.relative(path) for path in paths}
            known = {
                self.fs.relative(str(item.get("path") or ""))
                for item in entries
                if isinstance(item, dict)
            }
            unknown = sorted(selected - known)
            if unknown:
                raise ValueError(
                    "restore paths are not part of snapshot: " + ", ".join(unknown)
                )

        if expected_current is not None:
            current = self.diff(snapshot_id, conversation_id=conversation_id, turn_id=turn_id, paths=paths)
            for item in current:
                path = item["path"]
                if path not in expected_current or item["current_sha256"] != expected_current[path]:
                    raise ValueError(f"Rollback conflict: {path} changed after this mutation")
        restored: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            rel = self.fs.relative(str(item.get("path") or ""))
            if selected is not None and rel not in selected:
                continue
            existed = bool(item.get("existed"))
            if item.get("kind") == "directory":
                raw = self.artifacts.read(str(item.get("artifact_id") or ""))
                if str(item.get("sha256") or "").startswith("directory-v2:"):
                    self._restore_directory_v2(rel, raw, item["sha256"])
                    restored.append(rel)
                    continue
                if "directory:" + hashlib.sha256(raw).hexdigest() != item.get("sha256"):
                    raise ValueError(f"snapshot artifact digest mismatch for {rel}")
                from kitt.security.workspace_mutations import delete_path
                if self.fs.is_safe_directory(rel):
                    delete_path(self.fs, rel, recursive=True)
                elif self.fs.exists_regular(rel):
                    self.fs.unlink(rel, expected_sha256=None if expected_current is None else expected_current[rel])
                self.fs.create_directory(rel, parents=True, exist_ok=True)
                for entry in json.loads(raw):
                    target = rel + "/" + entry["path"]
                    if entry.get("directory"):
                        self.fs.create_directory(target, parents=True, exist_ok=True)
                    else:
                        self.fs.atomic_write(target, base64.b64decode(entry["content"], validate=True))
                restored.append(rel)
                continue
            if existed:
                artifact_id = str(item.get("artifact_id") or "")
                if not artifact_id:
                    raise ValueError(f"snapshot entry has no artifact for {rel}")
                raw = self.artifacts.read(artifact_id)
                expected = str(item.get("sha256") or "")
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError(f"snapshot artifact digest mismatch for {rel}")
                guard = {} if expected_current is None else {
                    "expected_sha256": expected_current[rel], "expected_exists": expected_current[rel] is not None,
                }
                self.fs.atomic_write(rel, raw, **guard)
                restored.append(rel)
            elif self.fs.exists_regular(rel):
                self.fs.unlink(rel, expected_sha256=None if expected_current is None else expected_current[rel])
                restored.append(rel)
            elif self.fs.is_safe_directory(rel):
                from kitt.security.workspace_mutations import delete_path
                delete_path(self.fs, rel, recursive=True)
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
