"""Semantic ranking over an already-bounded code candidate pool."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from kitt.context.candidates import ContextCandidate
from kitt.context.rag.embeddings import EmbeddingError, EmbeddingProvider


@dataclass(frozen=True)
class SemanticRankResult:
    candidates: tuple[ContextCandidate, ...]
    scores: dict[str, float]
    degraded: bool = False
    reason: str = ""


class SemanticCandidateReranker:
    """Embedding reranker that never scans or indexes repository files."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        max_candidates: int = 24,
        max_chars: int = 6000,
    ) -> None:
        self.provider = provider
        self.max_candidates = max(4, min(96, int(max_candidates)))
        self.max_chars = max(512, min(32000, int(max_chars)))

    def rank(
        self,
        query: str,
        candidates: Sequence[ContextCandidate],
    ) -> SemanticRankResult:
        pool = self._bounded_unique(candidates)[: self.max_candidates]
        if not query.strip() or not pool:
            return SemanticRankResult(tuple(), {})

        docs = [self._render_candidate(candidate) for candidate in pool]
        try:
            vectors = self.provider.embed([query[: self.max_chars], *docs])
        except (EmbeddingError, OSError, ValueError, RuntimeError) as exc:
            return SemanticRankResult(tuple(), {}, degraded=True, reason=str(exc)[:240])

        if len(vectors) != len(pool) + 1:
            return SemanticRankResult(
                tuple(), {}, degraded=True, reason="embedding count mismatch"
            )
        query_vector = vectors[0]
        if not query_vector:
            return SemanticRankResult(tuple(), {}, degraded=True, reason="empty query embedding")

        scored: list[tuple[float, ContextCandidate]] = []
        for candidate, vector in zip(pool, vectors[1:]):
            score = self._cosine(query_vector, vector)
            # Cosine can be [-1, 1]. Normalize for diagnostics, but ranking
            # still preserves the exact cosine order.
            normalized = max(0.0, min(1.0, (score + 1.0) / 2.0))
            scored.append((normalized, candidate))
        scored.sort(key=lambda item: item[0], reverse=True)
        return SemanticRankResult(
            candidates=tuple(candidate for _, candidate in scored),
            scores={candidate.candidate_id: score for score, candidate in scored},
        )

    def _render_candidate(self, candidate: ContextCandidate) -> str:
        header = (
            f"path: {candidate.path or '-'}\n"
            f"kind: {candidate.source_type}\n"
            f"representation: {candidate.representation}\n"
            f"retrieval_reason: {candidate.selection_reason}\n"
        )
        budget = max(0, self.max_chars - len(header))
        return header + candidate.content[:budget]

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or not left:
            return -1.0
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        denom = left_norm * right_norm
        if denom <= 1e-12:
            return -1.0
        return dot / denom

    @staticmethod
    def _bounded_unique(candidates: Sequence[ContextCandidate]) -> list[ContextCandidate]:
        seen: set[tuple[object, ...]] = set()
        result: list[ContextCandidate] = []
        # Keep the deterministic retrieval order; mandatory candidates do not
        # need semantic ranking and are selected separately by ContextSelector.
        for candidate in candidates:
            if candidate.mandatory:
                continue
            key = (
                candidate.path,
                candidate.start_line,
                candidate.end_line,
                candidate.content_hash,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result
