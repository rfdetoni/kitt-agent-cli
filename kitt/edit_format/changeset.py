from __future__ import annotations

import hashlib
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from kitt.domain.entities import ChangeSet, FileSnapshot
from kitt.edit_format.transaction import workspace_mutation_lock
from kitt.security.workspace_fs import WorkspaceFileSystem


@dataclass(frozen=True)
class _TrackedState:
    changeset: ChangeSet
    workspace_id: str
    conversation_id: str
    turn_id: str
    post_hashes: Dict[str, Optional[str]]
    post_exists: Dict[str, bool]
    post_contents: Dict[str, Optional[str]]


class ChangeSetTracker:
    """Session-scoped, optimistic and persistent undo journal.

    Both pre-edit and post-edit states are retained so an interrupted multi-file
    undo can itself be rolled back without guessing or overwriting an external
    modification.
    """

    def __init__(self, root_dir: str = ".", db=None, workspace_id: str = ""):
        self.root_dir = Path(root_dir).resolve()
        self.history: List[ChangeSet] = []
        self._tracked: dict[str, _TrackedState] = {}
        self.db = db
        self.workspace_id = str(workspace_id or "")
        self.max_changesets_per_session = 50
        self.max_total_bytes_per_workspace = 512 * 1024 * 1024
        self.ttl_days = 30
        self._lock = threading.RLock()

    def attach_db(self, db, workspace_id: str = "") -> None:
        self.db = db
        if workspace_id:
            self.workspace_id = str(workspace_id)

    def prune_retention(
        self,
        workspace_id: str = "",
        conversation_id: str = "",
    ) -> int:
        if not self.db:
            return 0
        ws = str(workspace_id or self.workspace_id or "").strip()
        if not ws:
            return 0
        deleted_count = 0
        cutoff_time = time.time() - (self.ttl_days * 86400.0)

        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 1. Delete changesets older than TTL
                res = conn.execute(
                    "DELETE FROM edit_changesets WHERE workspace_id=? AND created_at < ?",
                    (ws, cutoff_time),
                )
                deleted_count += res.rowcount or 0

                # 2. Delete excess REVERTED changesets (keep max 10 per session)
                if conversation_id:
                    reverted_rows = conn.execute(
                        "SELECT id FROM edit_changesets WHERE workspace_id=? AND conversation_id=? AND state='REVERTED' ORDER BY created_at DESC",
                        (ws, conversation_id),
                    ).fetchall()
                    if len(reverted_rows) > 10:
                        to_drop = [r[0] for r in reverted_rows[10:]]
                        conn.executemany("DELETE FROM edit_changesets WHERE id=?", [(i,) for i in to_drop])
                        deleted_count += len(to_drop)

                # 3. Delete APPLIED changesets beyond max_changesets_per_session
                if conversation_id:
                    applied_rows = conn.execute(
                        "SELECT id FROM edit_changesets WHERE workspace_id=? AND conversation_id=? AND state='APPLIED' ORDER BY created_at DESC",
                        (ws, conversation_id),
                    ).fetchall()
                    if len(applied_rows) > self.max_changesets_per_session:
                        to_drop = [r[0] for r in applied_rows[self.max_changesets_per_session:]]
                        conn.executemany("DELETE FROM edit_changesets WHERE id=?", [(i,) for i in to_drop])
                        deleted_count += len(to_drop)

                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return deleted_count

    def create_snapshot(self, relative_path: str) -> FileSnapshot:
        fs = WorkspaceFileSystem(self.root_dir)
        relative = fs.relative(relative_path)
        try:
            data = fs.read(relative)
        except FileNotFoundError:
            return FileSnapshot(relative_path=relative, existed=False, content=None)
        return FileSnapshot(
            relative_path=relative,
            existed=True,
            content=data.content.decode("utf-8", errors="ignore"),
        )

    @staticmethod
    def _sha256_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def record_changeset(
        self,
        description: str,
        snapshots: List[FileSnapshot],
        *,
        workspace_id: str = "",
        conversation_id: str = "",
        turn_id: str = "",
        post_hashes: Optional[Dict[str, Optional[str]]] = None,
        post_exists: Optional[Dict[str, bool]] = None,
        post_contents: Optional[Dict[str, Optional[str]]] = None,
    ) -> ChangeSet:
        cs = ChangeSet(
            id=uuid.uuid4().hex,
            timestamp=time.time(),
            description=description,
            snapshots=list(snapshots),
        )
        ws = str(workspace_id or self.workspace_id or "")
        conv = str(conversation_id or "")
        tid = str(turn_id or "")
        if bool(ws) != bool(conv):
            raise ValueError("Scoped changesets require both workspace_id and conversation_id")
        if ws and conv:
            for snap in snapshots:
                rel = snap.relative_path
                if rel not in (post_exists or {}):
                    raise ValueError(f"Post-edit existence missing for '{rel}'")
                if (post_exists or {}).get(rel):
                    if not (post_hashes or {}).get(rel):
                        raise ValueError(f"Post-edit hash missing for '{rel}'")
                    if (post_contents or {}).get(rel) is None:
                        raise ValueError(f"Post-edit content missing for '{rel}'")
        tracked = _TrackedState(
            changeset=cs,
            workspace_id=ws,
            conversation_id=conv,
            turn_id=tid,
            post_hashes=dict(post_hashes or {}),
            post_exists=dict(post_exists or {}),
            post_contents=dict(post_contents or {}),
        )
        # Persist before exposing it in the in-memory history. If persistence
        # fails, callers can roll back file mutations without a phantom entry.
        self._persist(tracked)
        with self._lock:
            self.history.append(cs)
            self._tracked[cs.id] = tracked
        return cs

    def _persist(self, tracked: _TrackedState) -> None:
        if not self.db or not tracked.workspace_id or not tracked.conversation_id:
            return
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """INSERT INTO edit_changesets
                       (id,workspace_id,conversation_id,turn_id,created_at,description,state)
                       VALUES(?,?,?,?,?,?, 'APPLIED')""",
                    (
                        tracked.changeset.id,
                        tracked.workspace_id,
                        tracked.conversation_id,
                        tracked.turn_id,
                        tracked.changeset.timestamp,
                        tracked.changeset.description,
                    ),
                )
                for snap in tracked.changeset.snapshots:
                    rel = snap.relative_path
                    post_exists = tracked.post_exists.get(rel, True)
                    post_hash = tracked.post_hashes.get(rel)
                    post_content = tracked.post_contents.get(rel)
                    if post_exists and (post_content is None or not post_hash):
                        raise ValueError(f"Post-edit state incomplete for '{rel}'")
                    conn.execute(
                        """INSERT INTO edit_change_snapshots
                           (changeset_id,relative_path,existed,content,
                            post_exists,post_sha256,post_content)
                           VALUES(?,?,?,?,?,?,?)""",
                        (
                            tracked.changeset.id,
                            rel,
                            1 if snap.existed else 0,
                            snap.content,
                            1 if post_exists else 0,
                            post_hash,
                            post_content,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        try:
            self.prune_retention(tracked.workspace_id, tracked.conversation_id)
        except Exception:
            pass

    def _load_latest(self, workspace_id: str, conversation_id: str) -> Optional[_TrackedState]:
        if not self.db:
            return None
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT id,turn_id,created_at,description
                   FROM edit_changesets
                   WHERE workspace_id=? AND conversation_id=? AND state='APPLIED'
                   ORDER BY created_at DESC,id DESC LIMIT 1""",
                (workspace_id, conversation_id),
            ).fetchone()
            if not row:
                return None
            snapshots = conn.execute(
                """SELECT relative_path,existed,content,post_exists,
                          post_sha256,post_content
                   FROM edit_change_snapshots WHERE changeset_id=?
                   ORDER BY relative_path ASC""",
                (row["id"],),
            ).fetchall()
        cs = ChangeSet(
            id=row["id"],
            timestamp=float(row["created_at"]),
            description=row["description"],
            snapshots=[
                FileSnapshot(s["relative_path"], bool(s["existed"]), s["content"])
                for s in snapshots
            ],
        )
        return _TrackedState(
            changeset=cs,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=row["turn_id"] or "",
            post_hashes={s["relative_path"]: s["post_sha256"] for s in snapshots},
            post_exists={s["relative_path"]: bool(s["post_exists"]) for s in snapshots},
            post_contents={s["relative_path"]: s["post_content"] for s in snapshots},
        )

    def _latest_in_memory(self, workspace_id: str, conversation_id: str) -> Optional[_TrackedState]:
        with self._lock:
            for cs in reversed(self.history):
                tracked = self._tracked.get(cs.id)
                if tracked and tracked.workspace_id == workspace_id and tracked.conversation_id == conversation_id:
                    return tracked
        return None

    def _verify_post_state(self, fs: WorkspaceFileSystem, tracked: _TrackedState) -> None:
        for snap in tracked.changeset.snapshots:
            rel = snap.relative_path
            expected_exists = tracked.post_exists.get(rel, True)
            expected_hash = tracked.post_hashes.get(rel)
            try:
                current = fs.read(rel)
                exists = True
            except FileNotFoundError:
                current = None
                exists = False
            if exists != expected_exists:
                raise RuntimeError(f"Undo refused: '{rel}' existence changed after KITT edit")
            if exists and expected_hash and current and current.sha256 != expected_hash:
                raise RuntimeError(f"Undo refused: '{rel}' changed after KITT edit")

    def _restore_pre_state(self, fs: WorkspaceFileSystem, tracked: _TrackedState, snap: FileSnapshot) -> None:
        rel = snap.relative_path
        post_exists = tracked.post_exists.get(rel, True)
        post_hash = tracked.post_hashes.get(rel)
        if snap.existed:
            if snap.content is None:
                raise RuntimeError(f"Undo snapshot for '{rel}' has no restorable content")
            fs.atomic_write(
                rel,
                snap.content,
                expected_exists=post_exists,
                expected_sha256=post_hash if post_exists else None,
            )
        elif post_exists:
            fs.unlink(rel, expected_exists=True, expected_sha256=post_hash)

    def _restore_post_state(self, fs: WorkspaceFileSystem, tracked: _TrackedState, snap: FileSnapshot) -> None:
        rel = snap.relative_path
        post_exists = tracked.post_exists.get(rel, True)
        post_hash = tracked.post_hashes.get(rel)
        if post_exists:
            post_content = tracked.post_contents.get(rel)
            if post_content is None:
                raise RuntimeError(f"Post-edit rollback content missing for '{rel}'")
            if snap.existed:
                if snap.content is None:
                    raise RuntimeError(f"Pre-edit content missing for '{rel}'")
                pre_hash = self._sha256_text(snap.content)
                fs.atomic_write(
                    rel,
                    post_content,
                    expected_exists=True,
                    expected_sha256=pre_hash,
                )
            else:
                fs.atomic_write(rel, post_content, expected_exists=False)
            # Defensive check; catches encoding/storage corruption.
            if fs.read(rel).sha256 != post_hash:
                raise RuntimeError(f"Post-edit rollback hash mismatch for '{rel}'")
        elif snap.existed:
            if snap.content is None:
                raise RuntimeError(f"Pre-edit content missing for '{rel}'")
            fs.unlink(
                rel,
                expected_exists=True,
                expected_sha256=self._sha256_text(snap.content),
            )

    def _mark_reverted(self, changeset_id: str) -> None:
        if self.db:
            with self.db.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    cur = conn.execute(
                        "UPDATE edit_changesets SET state='REVERTED' WHERE id=? AND state='APPLIED'",
                        (changeset_id,),
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError("Undo journal changed concurrently")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        with self._lock:
            self.history[:] = [cs for cs in self.history if cs.id != changeset_id]
            self._tracked.pop(changeset_id, None)

    def revert_last_changeset(
        self,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[ChangeSet]:
        ws = str(workspace_id or self.workspace_id or "")
        conv = str(conversation_id or "")
        if not ws or not conv:
            # Legacy local-only compatibility. Identity-bearing changesets must
            # never be undone without an explicit session/workspace scope.
            with self._lock:
                legacy = self.history[-1] if self.history else None
                tracked = self._tracked.get(legacy.id) if legacy else None
            if tracked and (tracked.workspace_id or tracked.conversation_id):
                raise ValueError("workspace_id and conversation_id are required for scoped undo")
            return legacy and self._legacy_revert(legacy)

        tracked = self._latest_in_memory(ws, conv) or self._load_latest(ws, conv)
        if tracked is None:
            return None

        fs = WorkspaceFileSystem(self.root_dir)
        with workspace_mutation_lock(self.root_dir):
            self._verify_post_state(fs, tracked)
            restored: list[FileSnapshot] = []
            try:
                for snap in reversed(tracked.changeset.snapshots):
                    self._restore_pre_state(fs, tracked, snap)
                    restored.append(snap)
            except Exception as exc:
                rollback_errors: list[str] = []
                for snap in reversed(restored):
                    try:
                        self._restore_post_state(fs, tracked, snap)
                    except Exception as rb_exc:
                        rollback_errors.append(f"{snap.relative_path}: {rb_exc}")
                suffix = f"; rollback failures: {'; '.join(rollback_errors)}" if rollback_errors else "; undo rollback completed"
                raise RuntimeError(f"Undo failed: {exc}{suffix}") from exc

            try:
                self._mark_reverted(tracked.changeset.id)
            except Exception as exc:
                rollback_errors: list[str] = []
                for snap in reversed(tracked.changeset.snapshots):
                    try:
                        self._restore_post_state(fs, tracked, snap)
                    except Exception as rb_exc:
                        rollback_errors.append(f"{snap.relative_path}: {rb_exc}")
                suffix = f"; rollback failures: {'; '.join(rollback_errors)}" if rollback_errors else "; file state restored"
                raise RuntimeError(f"Undo journal commit failed: {exc}{suffix}") from exc
            return tracked.changeset

    def _legacy_revert(self, cs: ChangeSet) -> ChangeSet:
        fs = WorkspaceFileSystem(self.root_dir)
        with workspace_mutation_lock(self.root_dir):
            for snap in reversed(cs.snapshots):
                if snap.existed and snap.content is not None:
                    fs.atomic_write(snap.relative_path, snap.content)
                elif not snap.existed:
                    try:
                        fs.unlink(snap.relative_path, expected_exists=None)
                    except FileNotFoundError:
                        pass
        with self._lock:
            if self.history and self.history[-1].id == cs.id:
                self.history.pop()
            self._tracked.pop(cs.id, None)
        return cs
