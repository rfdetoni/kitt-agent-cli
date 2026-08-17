"""Phase 4: PRUNE & INDEX — Salience scoring, lifecycle maintenance, and index rebuilding."""
from __future__ import annotations

import time
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from kitt.dreaming.models import (
    MemoryRecord,
    MemoryStatus,
    DreamSnapshot,
)
from kitt.dreaming.repository import MemoryRepository

PROTECTED_KINDS = {
    "PROJECT_RULE",
    "ARCHITECTURE_DECISION",
    "USER_PREFERENCE",
}


class DreamPruneAndIndexPhase:
    """Calculates memory salience, transitions stale unpinned memories to ARCHIVED, and updates projections."""

    def __init__(
        self,
        memory_repo: MemoryRepository,
        archive_threshold_days: float = 60.0,
        min_salience_archive: float = 0.15,
    ):
        self.memory_repo = memory_repo
        self.archive_threshold_days = archive_threshold_days
        self.min_salience_archive = min_salience_archive

    def prune_and_index(
        self,
        workspace_id: str,
        snapshot: DreamSnapshot,
        root_dir: Optional[Path] = None,
    ) -> Tuple[List[MemoryRecord], str]:
        """Calculates salience, flags low-salience memories for archival, and updates MEMORY.md."""
        now = time.time()
        updated_records: List[MemoryRecord] = []

        for mem in snapshot.memories:
            # 1. Skip pinned and protected active memories
            if mem.pinned or mem.kind in PROTECTED_KINDS and mem.status == "ACTIVE":
                continue

            # 2. Check if superseded memory should be archived
            if mem.status == "SUPERSEDED":
                age_days = (now - mem.updated_at) / 86400.0
                if age_days >= self.archive_threshold_days:
                    updated_records.append(
                        MemoryRecord(
                            id=mem.id,
                            workspace_id=mem.workspace_id,
                            kind=mem.kind,
                            content=mem.content,
                            normalized_content=mem.normalized_content,
                            status="ARCHIVED",
                            importance=mem.importance,
                            confidence=mem.confidence,
                            created_at=mem.created_at,
                            updated_at=now,
                            last_accessed_at=mem.last_accessed_at,
                            access_count=mem.access_count,
                            valid_from=mem.valid_from,
                            valid_until=mem.valid_until,
                            supersedes_id=mem.supersedes_id,
                            content_hash=mem.content_hash,
                            pinned=mem.pinned,
                            metadata_json=mem.metadata_json,
                        )
                    )
                continue

            # 3. Calculate salience for unpinned active transient memories
            if mem.status in ("ACTIVE", "CANDIDATE"):
                salience = self.calculate_salience(mem, now)
                age_days = (now - mem.created_at) / 86400.0
                if salience < self.min_salience_archive and age_days >= self.archive_threshold_days:
                    updated_records.append(
                        MemoryRecord(
                            id=mem.id,
                            workspace_id=mem.workspace_id,
                            kind=mem.kind,
                            content=mem.content,
                            normalized_content=mem.normalized_content,
                            status="ARCHIVED",
                            importance=mem.importance,
                            confidence=mem.confidence,
                            created_at=mem.created_at,
                            updated_at=now,
                            last_accessed_at=mem.last_accessed_at,
                            access_count=mem.access_count,
                            valid_from=mem.valid_from,
                            valid_until=mem.valid_until,
                            supersedes_id=mem.supersedes_id,
                            content_hash=mem.content_hash,
                            pinned=mem.pinned,
                            metadata_json=mem.metadata_json,
                        )
                    )

        # 4. Rebuild materialized MEMORY.md projection
        projection = self.memory_repo.rebuild_materialized_view(workspace_id, root_dir=root_dir)

        return updated_records, projection

    @staticmethod
    def calculate_salience(mem: MemoryRecord, now: Optional[float] = None) -> float:
        """Simple, deterministic salience formula: importance * confidence * recency_factor * usage_factor."""
        t = now or time.time()
        age_seconds = max(0.0, t - mem.updated_at)
        age_days = age_seconds / 86400.0

        # Recency factor with 30-day gentle decay (floor at 0.20)
        recency_factor = max(0.20, 1.0 - (age_days / 60.0))

        # Usage factor based on access count (capped at 1.50)
        usage_factor = min(1.50, 1.0 + (mem.access_count * 0.05))

        return round(mem.importance * mem.confidence * recency_factor * usage_factor, 4)
