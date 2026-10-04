from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Optional

from kitt.artifacts.models import Artifact
from kitt.history.database import HistoryDatabase


class ArtifactStore:
    def __init__(self, root_dir: str, db: HistoryDatabase,
                 inline_limit: int = 32 * 1024,
                 max_artifact_bytes: int = 8 * 1024 * 1024,
                 page_bytes: int = 32 * 1024,
                 ephemeral: bool = False):
        self.db = db
        self.inline_limit = inline_limit
        self.max_artifact_bytes = max_artifact_bytes
        self.page_bytes = max(1024, page_bytes)
        self.ephemeral = ephemeral
        self._tmp_root: Optional[str] = None
        if ephemeral:
            self._tmp_root = tempfile.mkdtemp(prefix="kitt-artifacts-")
            self.root = Path(self._tmp_root)
            self.storage = self.root / "artifacts"
            self.storage.mkdir(parents=True, exist_ok=True)
        else:
            self.root = Path(root_dir).resolve()
            self.storage = self.root / ".kitt" / "artifacts"
            self.storage.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _from_row(row) -> Artifact:
        return Artifact(
            id=row["id"], workspace_id=row["workspace_id"],
            conversation_id=row["conversation_id"], turn_id=row["turn_id"],
            artifact_type=row["artifact_type"], storage_kind=row["storage_kind"],
            relative_storage_path=row["relative_storage_path"],
            inline_content=row["inline_content"], summary=row["summary"],
            content_hash=row["content_hash"], size_bytes=row["size_bytes"],
            sensitivity=row["sensitivity"], created_at=row["created_at"],
            expires_at=row["expires_at"], pinned=bool(row["pinned"]),
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    def _validate_ownership(self, conn, workspace_id: str,
                            conversation_id: Optional[str],
                            turn_id: Optional[str]) -> None:
        """Validate that workspace/conversation/turn belong to each other."""
        ws = conn.execute("SELECT id FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        if not ws:
            raise sqlite3.IntegrityError(f"Unknown workspace id {workspace_id}")
        if conversation_id:
            conv = conn.execute(
                "SELECT workspace_id FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            if not conv:
                raise sqlite3.IntegrityError(f"Unknown conversation id {conversation_id}")
            if conv["workspace_id"] != workspace_id:
                raise sqlite3.IntegrityError(
                    f"Conversation {conversation_id} does not belong to workspace {workspace_id}"
                )
        if turn_id:
            if not conversation_id:
                raise sqlite3.IntegrityError("turn_id requires conversation_id")
            turn = conn.execute(
                "SELECT conversation_id FROM turns WHERE id=?", (turn_id,)
            ).fetchone()
            if not turn:
                raise sqlite3.IntegrityError(f"Unknown turn id {turn_id}")
            if turn["conversation_id"] != conversation_id:
                raise sqlite3.IntegrityError(
                    f"Turn {turn_id} does not belong to conversation {conversation_id}"
                )

    def put(self, workspace_id: str, content, artifact_type: str, summary: str,
            conversation_id: Optional[str] = None, turn_id: Optional[str] = None,
            sensitivity: str = "NORMAL", metadata: Optional[dict] = None,
            expires_at: Optional[float] = None) -> Artifact:
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        if len(raw) > self.max_artifact_bytes:
            raise ValueError("Artifact exceeds size limit")
        digest = hashlib.sha256(raw).hexdigest()
        aid = f"art_{uuid.uuid4().hex}"
        kind = "INLINE"
        rel: Optional[str] = None
        inline: Optional[bytes] = raw
        staged_tmp: Optional[Path] = None
        if len(raw) > self.inline_limit:
            kind, rel, inline = "FILE", f"{digest[:2]}/{digest}.bin", None
            target = self.storage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # Stage into a temp file *before* touching the DB so a failed
            # insert does not leave an unreferenced blob behind.
            tmp = target.with_suffix(".tmp-" + uuid.uuid4().hex[:8])
            tmp.write_bytes(raw)
            staged_tmp = tmp
        now = time.time()
        try:
            with self.db.get_connection() as conn:
                self._validate_ownership(conn, workspace_id, conversation_id, turn_id)
                conn.execute("""INSERT INTO artifacts
                    (id,workspace_id,conversation_id,turn_id,artifact_type,storage_kind,
                     relative_storage_path,inline_content,summary,content_hash,size_bytes,
                     sensitivity,created_at,expires_at,pinned,metadata_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
                    (aid, workspace_id, conversation_id, turn_id, artifact_type, kind, rel, inline,
                     summary, digest, len(raw), sensitivity, now, expires_at,
                     json.dumps(metadata or {}, ensure_ascii=False)))
        except Exception:
            if staged_tmp is not None and staged_tmp.exists():
                staged_tmp.unlink(missing_ok=True)
            raise
        # Commit the staged blob only after the row exists.
        if staged_tmp is not None:
            if rel is None:
                raise RuntimeError("staged artifact is missing storage path")
            target = self.storage / rel
            try:
                os.replace(staged_tmp, target)
            except OSError:
                staged_tmp.unlink(missing_ok=True)
                # The DB row references a missing blob; remove it to avoid
                # dangling references and surface the failure.
                with self.db.get_connection() as conn:
                    conn.execute("DELETE FROM artifacts WHERE id=?", (aid,))
                raise
        return self.get(aid)

    def get(self, artifact_id: str) -> Optional[Artifact]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            return self._from_row(row) if row else None

    def read(self, artifact_id: str) -> bytes:
        artifact = self.get(artifact_id)
        if not artifact:
            raise KeyError(artifact_id)
        if artifact.expires_at and artifact.expires_at < time.time() and not artifact.pinned:
            raise KeyError("Artifact expired")
        raw = artifact.inline_content if artifact.storage_kind == "INLINE" else (
            self.storage / artifact.relative_storage_path
        ).read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact.content_hash:
            raise ValueError("Artifact integrity check failed")
        return raw

    def read_text(self, artifact_id: str) -> str:
        return self.read(artifact_id).decode("utf-8", errors="ignore")

    def read_page(self, artifact_id: str, offset: int = 0,
                  max_bytes: Optional[int] = None) -> bytes:
        """Read a bounded page of the artifact (byte pagination)."""
        artifact = self.get(artifact_id)
        if not artifact:
            raise KeyError(artifact_id)
        if artifact.expires_at and artifact.expires_at < time.time() and not artifact.pinned:
            raise KeyError("Artifact expired")
        offset = max(0, offset)
        limit = max(1, min(max_bytes or self.page_bytes, self.page_bytes))
        raw = artifact.inline_content if artifact.storage_kind == "INLINE" else (
            self.storage / artifact.relative_storage_path
        ).read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact.content_hash:
            raise ValueError("Artifact integrity check failed")
        return raw[offset:offset + limit]

    def read_text_page(self, artifact_id: str, offset: int = 0,
                       max_bytes: Optional[int] = None) -> dict:
        raw = self.read_page(artifact_id, offset, max_bytes)
        artifact = self.get(artifact_id)
        if artifact is None:
            raise KeyError(artifact_id)
        total = artifact.size_bytes
        has_more = offset + len(raw) < total
        return {
            "content": raw.decode("utf-8", errors="replace"),
            "offset": offset,
            "bytes_returned": len(raw),
            "total_bytes": total,
            "has_more": has_more,
            "content_hash": artifact.content_hash,
        }

    def search_text(
        self,
        artifact_id: str,
        query: str,
        *,
        limit: int = 20,
        context_chars: int = 160,
    ) -> list[dict]:
        """Search text with bounded memory; FILE artifacts are never fully hydrated."""
        artifact = self.get(artifact_id)
        if not artifact:
            raise KeyError(artifact_id)
        if artifact.expires_at and artifact.expires_at < time.time() and not artifact.pinned:
            raise KeyError("Artifact expired")

        needle = str(query or "").strip().casefold()
        if not needle:
            return []
        limit = max(1, min(int(limit), 100))
        context_chars = max(40, min(int(context_chars), 2000))

        hits: list[dict] = []
        if artifact.storage_kind == "INLINE":
            inline_raw = bytes(artifact.inline_content or b"")
            if hashlib.sha256(inline_raw).hexdigest() != artifact.content_hash:
                raise ValueError("Artifact integrity check failed")
            text = inline_raw.decode(
                "utf-8",
                errors="replace",
            )
            folded = text.casefold()
            start = 0
            while len(hits) < limit:
                index = folded.find(needle, start)
                if index < 0:
                    break
                left = max(0, index - context_chars)
                right = min(len(text), index + len(needle) + context_chars)
                hits.append(
                    {
                        "offset": index,
                        "excerpt": text[left:right],
                        "content_hash": artifact.content_hash,
                    }
                )
                start = index + max(1, len(needle))
            return hits

        path = self.storage / str(artifact.relative_storage_path or "")
        digest = hashlib.sha256()
        with path.open("rb") as raw_file:
            for block in iter(lambda: raw_file.read(64 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != artifact.content_hash:
            raise ValueError("Artifact integrity check failed")

        # Keep only a bounded rolling window. The retained prefix provides left
        # context while the unprocessed suffix guarantees enough right context
        # for any hit emitted from the safe region.
        chunk_chars = max(4096, context_chars * 4, len(needle) * 4)
        keep_chars = context_chars + max(1, len(needle))
        buffer = ""
        base_offset = 0
        next_search_offset = 0

        with path.open("r", encoding="utf-8", errors="replace") as text_file:
            eof = False
            while not eof and len(hits) < limit:
                chunk = text_file.read(chunk_chars)
                eof = chunk == ""
                buffer += chunk
                safe_end = len(buffer) if eof else max(0, len(buffer) - keep_chars)
                folded = buffer.casefold()
                local_start = max(0, next_search_offset - base_offset)

                while len(hits) < limit:
                    index = folded.find(needle, local_start)
                    if index < 0 or (not eof and index >= safe_end):
                        break
                    left = max(0, index - context_chars)
                    right = min(
                        len(buffer),
                        index + len(needle) + context_chars,
                    )
                    hits.append(
                        {
                            "offset": base_offset + index,
                            "excerpt": buffer[left:right],
                            "content_hash": artifact.content_hash,
                        }
                    )
                    local_start = index + max(1, len(needle))
                    next_search_offset = base_offset + local_start

                if eof:
                    break

                trim = max(0, safe_end - context_chars)
                if trim:
                    buffer = buffer[trim:]
                    base_offset += trim

        return hits

    def list(self, conversation_id: Optional[str] = None, limit: int = 20,
             offset: int = 0, workspace_id: Optional[str] = None) -> List[Artifact]:
        conditions: list[str] = []
        args: list[object] = []
        if workspace_id:
            conditions.append("workspace_id=?")
            args.append(workspace_id)
        if conversation_id:
            conditions.append("conversation_id=?")
            args.append(conversation_id)

        query = "SELECT * FROM artifacts"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?"
        args += [min(max(limit, 1), 100), max(offset, 0)]
        with self.db.get_connection() as conn:
            return [self._from_row(r) for r in conn.execute(query, args).fetchall()]

    def pin(self, artifact_id: str, pinned: bool = True) -> bool:
        with self.db.get_connection() as conn:
            cur = conn.execute("UPDATE artifacts SET pinned=? WHERE id=?", (int(pinned), artifact_id))
            return cur.rowcount == 1

    def collect_garbage(self, now: Optional[float] = None) -> int:
        """Remove expired artifacts without deleting shared content-addressed blobs."""
        now = now or time.time()
        removed = 0
        orphaned_paths: set[str] = set()
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE pinned=0 AND expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            ).fetchall()
            for r in rows:
                a = self._from_row(r)
                conn.execute("DELETE FROM artifacts WHERE id=?", (a.id,))
                removed += 1
                if a.storage_kind == "FILE" and a.relative_storage_path:
                    still_referenced = conn.execute(
                        """SELECT 1 FROM artifacts
                           WHERE storage_kind='FILE' AND relative_storage_path=?
                           LIMIT 1""",
                        (a.relative_storage_path,),
                    ).fetchone()
                    if not still_referenced:
                        orphaned_paths.add(a.relative_storage_path)
        for relative_path in orphaned_paths:
            target = self.storage / relative_path
            if target.exists():
                target.unlink()
        return removed

    def close(self) -> None:
        if self.ephemeral and self._tmp_root:
            shutil.rmtree(self._tmp_root, ignore_errors=True)
            self._tmp_root = None
