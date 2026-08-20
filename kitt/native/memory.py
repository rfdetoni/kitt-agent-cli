from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from .storage import NativeStateRepository

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.+#$-]{2,}")


class Embedder(Protocol):
    name: str
    dimensions: int
    def embed(self, text: str) -> list[float]: ...


class HashProjectionEmbedder:
    """Deterministic, zero-dependency feature projection used only as a fallback.

    It is not presented as a semantic model.  It supplies a stable similarity
    signal across token/character features when no real embedding provider is
    configured, keeping local/offline KITT fully functional.
    """
    name = "kitt-hash-projection-v1"
    dimensions = 256

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        normalized = text.casefold()
        features = list(_TOKEN_RE.findall(normalized))
        features += [normalized[i:i+3] for i in range(max(0, len(normalized) - 2))]
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "little")
            index = value % self.dimensions
            sign = 1.0 if (value >> 63) == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(x*x for x in vec)) or 1.0
        return [x / norm for x in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return max(-1.0, min(1.0, sum(x*y for x, y in zip(a, b))))


def _tokens(text: str) -> set[str]:
    return {t.casefold() for t in _TOKEN_RE.findall(text)}


def _lexical(query: set[str], text: str) -> float:
    if not query:
        return 0.0
    target = _tokens(text)
    if not target:
        return 0.0
    overlap = len(query & target)
    exact_bonus = 0.25 if " ".join(query) in text.casefold() else 0.0
    return min(1.0, overlap / max(1, min(len(query), 8)) + exact_bonus)


@dataclass(frozen=True)
class RankedMemory:
    source: str
    id: str
    text: str
    score: float
    lexical_score: float
    vector_score: float
    salience: float
    metadata: dict[str, Any]


class HybridMemoryService:
    """Adds hybrid recall, durable concepts and corrections to KITT memory.

    Existing MemoryManager methods remain available via delegation, so this can
    replace the service reference without changing callers such as TurnProcessor.
    """

    def __init__(self, base_manager: Any, memory_repo: Any, native_repo: NativeStateRepository,
                 embedder: Embedder | None = None):
        self.base = base_manager
        self.memory_repo = memory_repo
        self.native_repo = native_repo
        self.workspace_id = native_repo.workspace_id
        self.embedder: Embedder = embedder or HashProjectionEmbedder()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def _ensure_memory_vectors(self, memories: Iterable[Any]) -> dict[str, list[float]]:
        records = list(memories)
        existing = self.native_repo.get_vectors([m.id for m in records])
        for memory in records:
            if memory.id in existing:
                continue
            vector = self.embedder.embed(memory.normalized_content or memory.content)
            self.native_repo.put_vector(memory.id, vector, self.embedder.name)
            existing[memory.id] = vector
        return existing

    @staticmethod
    def _salience(memory: Any) -> float:
        importance = float(getattr(memory, "importance", 0.5) or 0.5)
        confidence = float(getattr(memory, "confidence", 0.5) or 0.5)
        access = int(getattr(memory, "access_count", 0) or 0)
        pinned = bool(getattr(memory, "pinned", False))
        return min(1.0, importance * confidence * min(1.35, 1.0 + access * 0.04) + (0.15 if pinned else 0.0))

    def query(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 50))
        q_tokens = _tokens(query)
        q_vector = self.embedder.embed(query)
        memories = list(self.memory_repo.get_active_memories(self.workspace_id))
        vectors = self._ensure_memory_vectors(memories)
        ranked: list[RankedMemory] = []

        for memory in memories:
            text = memory.content
            lexical = _lexical(q_tokens, text)
            vector = max(0.0, _cosine(q_vector, vectors.get(memory.id, [])))
            salience = self._salience(memory)
            task = 0.15 if any(token in text.casefold() for token in q_tokens) else 0.0
            score = 0.34 * lexical + 0.36 * vector + 0.20 * salience + 0.10 * task
            if getattr(memory, "pinned", False):
                score += 0.10
            ranked.append(RankedMemory(
                "memory", memory.id, text, min(1.5, score), lexical, vector, salience,
                {"kind": memory.kind, "pinned": bool(memory.pinned), "confidence": memory.confidence},
            ))

        for concept in self.native_repo.search_concepts(query, limit=max(limit * 2, 8)):
            text = f"{concept['name']}: {concept['definition']}"
            lexical = _lexical(q_tokens, text)
            vector = max(0.0, _cosine(q_vector, self.embedder.embed(text)))
            score = 0.42 * lexical + 0.38 * vector + 0.20 * float(concept["confidence"])
            ranked.append(RankedMemory(
                "knowledge", concept["id"], text, score, lexical, vector, float(concept["confidence"]), concept,
            ))

        for correction in self.native_repo.list_corrections():
            text = f"When {correction['context']}: prefer {correction['corrected']} instead of {correction['predicted']}"
            lexical = _lexical(q_tokens, text)
            vector_data = correction.get("vector") or self.embedder.embed(text)
            vector = max(0.0, _cosine(q_vector, vector_data))
            applied = min(0.15, int(correction.get("applied_count", 0)) * 0.02)
            score = 0.42 * lexical + 0.43 * vector + 0.15 + applied
            if lexical > 0.0 or vector > 0.15:
                ranked.append(RankedMemory(
                    "correction", correction["id"], text, score, lexical, vector, 1.0, correction,
                ))

        ranked.sort(key=lambda item: (item.score, item.salience), reverse=True)
        selected = ranked[:limit]
        memory_ids = [item.id for item in selected if item.source == "memory"]
        if memory_ids:
            try:
                self.memory_repo.touch_memory_access(memory_ids)
            except Exception:
                pass
        for item in selected:
            if item.source == "correction":
                try:
                    self.native_repo.mark_correction_applied(item.id)
                except Exception:
                    pass
        return [{
            "source": item.source, "id": item.id, "text": item.text,
            "score": round(item.score, 5), "lexical_score": round(item.lexical_score, 5),
            "vector_score": round(item.vector_score, 5), "salience": round(item.salience, 5),
            "metadata": item.metadata,
        } for item in selected]

    def get_memory_context(self, prompt: str = "", max_tokens: int = 400) -> str:
        if not prompt:
            return self.base.get_memory_context(prompt, max_tokens)
        rows = self.query(prompt, limit=8)
        lines: list[str] = []
        used = 0
        for row in rows:
            line = f"- [{row['source'].upper()}] {row['text']}"
            cost = (len(line) + 3) // 4
            if used + cost > max_tokens:
                continue
            lines.append(line); used += cost
        return "\n".join(lines)

    def get_relevant_memories(self, prompt: str) -> list[Any]:
        # Preserve compatibility for callers that require MemoryItem objects.
        try:
            from kitt.memory.memory_manager import MemoryItem
            return [MemoryItem(text=row["text"], scope="PROJECT", priority=3 if row["source"] != "memory" else 2)
                    for row in self.query(prompt, limit=8)]
        except Exception:
            return self.base.get_relevant_memories(prompt)

    def remember_correction(self, context: str, predicted: str, corrected: str,
                            reason: str | None = None, source: str = "user") -> str:
        text = f"{context} {predicted} {corrected} {reason or ''}"
        return self.native_repo.add_correction(
            context, predicted, corrected, reason, source, self.embedder.embed(text)
        )

    def remember_concept(self, name: str, definition: str, confidence: float = 0.7,
                         labels: Iterable[str] = (), source_memory_ids: Iterable[str] = ()) -> dict[str, Any]:
        concept = self.native_repo.upsert_concept(name, definition, confidence, labels, source_memory_ids)
        return concept.__dict__.copy()

    def link_concepts(self, source_id: str, target_id: str, relation: str, weight: float = 1.0) -> str:
        return self.native_repo.add_link(source_id, target_id, relation, weight)

    def refresh_after_dream(self) -> None:
        memories = list(self.memory_repo.get_active_memories(self.workspace_id))
        self._ensure_memory_vectors(memories)
        # Only durable, high-confidence project knowledge is promoted automatically.
        for memory in memories:
            if memory.kind not in {"PROJECT_RULE", "ARCHITECTURE_DECISION"}:
                continue
            if float(memory.confidence) < 0.85:
                continue
            name = f"{memory.kind.lower()}:{memory.content_hash[:12]}"
            self.native_repo.upsert_concept(
                name=name,
                definition=memory.content,
                confidence=float(memory.confidence),
                labels=(f"kind:{memory.kind.lower()}",),
                source_memory_ids=(memory.id,),
            )
