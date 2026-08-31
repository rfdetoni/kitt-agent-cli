"""Semantic ranking over an already-bounded code candidate pool."""
from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
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
    """Embedding reranker with a small document-only LRU cache.

    Query embeddings are never cached; document embeddings are keyed by rendered
    candidate content and provider identity so unchanged code does not repeatedly
    consume local-model latency across turns.
    """

    def __init__(
        self,
        provider: EmbeddingProvider,
        max_candidates: int = 24,
        max_chars: int = 6000,
        cache_size: int = 256,
    ) -> None:
        self.provider = provider
        self.max_candidates = max(4, min(96, int(max_candidates)))
        self.max_chars = max(512, min(32000, int(max_chars)))
        self.cache_size = max(0, min(4096, int(cache_size)))
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._provider_namespace = self._provider_key(provider)

    @staticmethod
    def _provider_key(provider: EmbeddingProvider) -> str:
        parts = [provider.__class__.__module__, provider.__class__.__qualname__]
        for name in ("model", "base_url"):
            value = getattr(provider, name, None)
            if value:
                parts.append(str(value))
        return "|".join(parts)

    def rank(
        self,
        query: str,
        candidates: Sequence[ContextCandidate],
    ) -> SemanticRankResult:
        pool = self._bounded_unique(candidates)[: self.max_candidates]
        if not query.strip() or not pool:
            return SemanticRankResult(tuple(), {})

        docs = [self._render_candidate(candidate) for candidate in pool]
        keys = [self._cache_key(candidate, doc) for candidate, doc in zip(pool, docs)]
        cached_vectors: list[list[float] | None] = [self._cache_get(key) for key in keys]
        missing_indexes = [idx for idx, vector in enumerate(cached_vectors) if vector is None]

        inputs = [query[: self.max_chars], *(docs[idx] for idx in missing_indexes)]
        try:
            fetched = self.provider.embed(inputs)
            if len(fetched) != len(inputs):
                raise EmbeddingError("embedding count mismatch")
            query_vector = self._validated_vector(fetched[0], "query")
            for offset, idx in enumerate(missing_indexes, start=1):
                vector = self._validated_vector(fetched[offset], f"candidate:{pool[idx].candidate_id}")
                cached_vectors[idx] = vector
                self._cache_put(keys[idx], vector)
        except (EmbeddingError, OSError, ValueError, RuntimeError) as exc:
            return SemanticRankResult(tuple(), {}, degraded=True, reason=str(exc)[:240])

        scored: list[tuple[float, ContextCandidate]] = []
        for candidate, vector in zip(pool, cached_vectors):
            if vector is None:
                return SemanticRankResult(
                    tuple(), {}, degraded=True, reason="missing cached embedding"
                )
            cosine = self._cosine(query_vector, vector)
            if not math.isfinite(cosine):
                return SemanticRankResult(
                    tuple(), {}, degraded=True, reason="non-finite cosine score"
                )
            normalized = max(0.0, min(1.0, (cosine + 1.0) / 2.0))
            scored.append((normalized, candidate))
        scored.sort(key=lambda item: item[0], reverse=True)
        return SemanticRankResult(
            candidates=tuple(candidate for _, candidate in scored),
            scores={candidate.candidate_id: score for score, candidate in scored},
        )

    @staticmethod
    def _validated_vector(vector: Sequence[float], label: str) -> list[float]:
        if not vector:
            raise EmbeddingError(f"empty {label} embedding")
        parsed = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in parsed):
            raise EmbeddingError(f"non-finite {label} embedding")
        return parsed

    def _render_candidate(self, candidate: ContextCandidate) -> str:
        header = (
            f"path: {candidate.path or '-'}\n"
            f"kind: {candidate.source_type}\n"
            f"representation: {candidate.representation}\n"
            f"retrieval_reason: {candidate.selection_reason}\n"
        )
        budget = max(0, self.max_chars - len(header))
        return header + candidate.content[:budget]

    def _cache_key(self, candidate: ContextCandidate, rendered: str) -> str:
        material = (
            f"{self._provider_namespace}\0{candidate.content_hash}\0"
            f"{hashlib.sha256(rendered.encode('utf-8')).hexdigest()}"
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> list[float] | None:
        if self.cache_size <= 0:
            return None
        with self._cache_lock:
            value = self._cache.get(key)
            if value is None:
                return None
            self._cache.move_to_end(key)
            return list(value)

    def _cache_put(self, key: str, vector: Sequence[float]) -> None:
        if self.cache_size <= 0:
            return
        with self._cache_lock:
            self._cache[key] = tuple(vector)
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or not left:
            return -1.0
        if not all(math.isfinite(value) for value in (*left, *right)):
            return float("nan")
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
