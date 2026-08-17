"""High-relevance memory retriever for task context injection."""
from __future__ import annotations

import re
from typing import List, Tuple, Optional, Set, Dict

from kitt.dreaming.models import MemoryRecord
from kitt.dreaming.repository import MemoryRepository


class MemoryRetriever:
    """Retrieves top relevant active memories for a given task prompt or Task IR."""

    def __init__(self, memory_repo: MemoryRepository, max_memories: int = 6):
        self.memory_repo = memory_repo
        self.max_memories = max_memories

    def retrieve(
        self,
        workspace_id: str,
        prompt: str = "",
        candidate_paths: Tuple[str, ...] = (),
        candidate_symbols: Tuple[str, ...] = (),
        touch_access: bool = True,
    ) -> List[MemoryRecord]:
        """Returns top 3-8 relevant active memories matching prompt, paths, or symbols."""
        active = self.memory_repo.get_active_memories(workspace_id)
        if not active:
            return []

        words = set(re.findall(r'[a-zA-Z0-9_\-]{3,}', prompt.lower()))
        for p in candidate_paths:
            words.update(re.findall(r'[a-zA-Z0-9_\-]{3,}', p.lower()))
        for s in candidate_symbols:
            words.update(re.findall(r'[a-zA-Z0-9_\-]{3,}', s.lower()))

        scored: List[Tuple[float, MemoryRecord]] = []
        for mem in active:
            mem_words = set(re.findall(r'[a-zA-Z0-9_\-]{3,}', mem.normalized_content.lower()))
            overlap = len(words.intersection(mem_words)) if words else 0

            # Base score from importance and confidence
            base_score = (mem.importance * 2.0) + mem.confidence
            if mem.pinned:
                base_score += 3.0  # Pinned rules have high baseline priority
            if mem.kind in ("PROJECT_RULE", "ARCHITECTURE_DECISION"):
                base_score += 1.5

            total_score = base_score + (overlap * 1.5)
            scored.append((total_score, mem))

        # Sort descending by score
        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [mem for _score, mem in scored[:self.max_memories]]

        if touch_access and selected:
            self.memory_repo.touch_memory_access([m.id for m in selected])

        return selected
