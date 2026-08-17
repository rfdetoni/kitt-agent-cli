"""Phase 1: ORIENT — 100% deterministic mapping of current memory and session state."""
from __future__ import annotations

import time
from typing import List, Tuple, Optional, Dict, Any

from kitt.dreaming.models import (
    DreamSnapshot,
    SessionDigest,
    SessionEntryDigest,
    MemoryRecord,
)
from kitt.dreaming.repository import MemoryRepository
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.history.session_tree import SessionTreeRepository


class DreamOrientPhase:
    """Deterministic, bounded inspection of workspace memory and recent session entries."""

    def __init__(
        self,
        db: HistoryDatabase,
        memory_repo: MemoryRepository,
        history_repo: HistoryRepository,
        session_tree: SessionTreeRepository,
        max_sessions: int = 20,
        max_entries: int = 200,
    ):
        self.db = db
        self.memory_repo = memory_repo
        self.history_repo = history_repo
        self.session_tree = session_tree
        self.max_sessions = max_sessions
        self.max_entries = max_entries

    def orient(self, workspace_id: str) -> DreamSnapshot:
        """Executes the ORIENT phase and builds an immutable DreamSnapshot."""
        now = time.time()

        # 1. Fetch current memory state from SQLite
        all_memories = self.memory_repo.get_all_memories(workspace_id)
        last_dream = self.memory_repo.get_last_dream_run(workspace_id)
        last_dream_at = last_dream.finished_at or last_dream.started_at if last_dream else None

        # 2. Query completed/recent conversations in this workspace since last dream
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if last_dream_at:
                cursor.execute(
                    """
                    SELECT id, created_at, updated_at, status
                    FROM conversations
                    WHERE workspace_id = ? AND updated_at >= ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (workspace_id, last_dream_at, self.max_sessions)
                )
            else:
                cursor.execute(
                    """
                    SELECT id, created_at, updated_at, status
                    FROM conversations
                    WHERE workspace_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (workspace_id, self.max_sessions)
                )
            conv_rows = cursor.fetchall()

        # Count completed sessions since last dream
        completed_count = sum(1 for r in conv_rows if (dict(r).get("status") or "") != "ACTIVE")

        # 3. Extract SessionDigest and SessionEntryDigest deterministically
        session_digests: List[SessionDigest] = []
        entry_digests: List[SessionEntryDigest] = []
        total_entries = 0

        for r in conv_rows:
            d = dict(r)
            conv_id = d["id"]
            started_at = float(d["created_at"])
            completed_at = float(d["updated_at"]) if d.get("status") != "ACTIVE" else None

            # Fetch active path / entries from SessionTree
            entries = self.session_tree.get_active_path(conv_id)
            user_reqs = []
            decisions = []
            failures = []
            validations = []
            changed_files = set()
            session_entry_ids = []

            for entry in entries:
                if total_entries >= self.max_entries:
                    break
                total_entries += 1
                session_entry_ids.append(entry.id)

                etype = entry.entry_type
                payload = entry.payload if isinstance(entry.payload, dict) else {}
                summary = ""

                if etype in ("USER_TURN", "USER_PROMPT", "PROMPT"):
                    txt = str(payload.get("content") or payload.get("prompt") or payload.get("text") or "").strip()
                    if txt:
                        user_reqs.append(txt[:300])
                        summary = f"User Request: {txt[:160]}"
                elif etype == "DECISION":
                    txt = str(payload.get("text") or payload.get("content") or "").strip()
                    if txt:
                        decisions.append(txt[:300])
                        summary = f"Decision: {txt[:160]}"
                elif etype == "TOOL_EXECUTION":
                    tname = payload.get("tool_name")
                    err = payload.get("error")
                    paths = payload.get("paths") or []
                    if isinstance(paths, list):
                        changed_files.update(paths)
                    if payload.get("path"):
                        changed_files.add(str(payload.get("path")))
                    if err:
                        failures.append(f"Tool {tname} failed: {str(err)[:200]}")
                        summary = f"Tool failure: {tname} - {str(err)[:120]}"
                    else:
                        summary = f"Tool: {tname}"
                elif etype in ("VALIDATION", "TEST_RESULT"):
                    passed = payload.get("passed", True)
                    txt = str(payload.get("details") or payload.get("output") or "").strip()
                    if not passed:
                        failures.append(f"Validation failed: {txt[:200]}")
                        summary = f"Validation failed: {txt[:120]}"
                    else:
                        validations.append(f"Validation passed: {txt[:160]}")
                        summary = f"Validation passed: {txt[:120]}"
                elif etype == "COMPACTION":
                    summary = f"Compaction summary: {str(payload.get('summary', ''))[:120]}"
                else:
                    txt = str(payload.get("content") or payload.get("summary") or "")
                    summary = f"{etype}: {txt[:100]}"

                entry_digests.append(
                    SessionEntryDigest(
                        entry_id=entry.id,
                        conversation_id=conv_id,
                        turn_id=entry.turn_id,
                        entry_type=etype,
                        summary_text=summary or etype,
                        created_at=entry.created_at,
                    )
                )

            session_digests.append(
                SessionDigest(
                    conversation_id=conv_id,
                    started_at=started_at,
                    completed_at=completed_at,
                    user_requests=tuple(user_reqs),
                    decisions=tuple(decisions),
                    failures=tuple(failures),
                    validations=tuple(validations),
                    changed_files=tuple(sorted(changed_files)),
                    entry_ids=tuple(session_entry_ids),
                )
            )

        return DreamSnapshot(
            workspace_id=workspace_id,
            memories=tuple(all_memories),
            recent_sessions=tuple(session_digests),
            recent_entries=tuple(entry_digests),
            last_dream_at=last_dream_at,
            completed_sessions_since_last_dream=completed_count,
            generated_at=now,
        )
