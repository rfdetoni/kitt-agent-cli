"""Reciprocal Rank Fusion (RRF) for heterogeneous code retrieval signals."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Sequence

from kitt.context.candidates import ContextCandidate


@dataclass(frozen=True)
class RankedSource:
    name: str
    candidates: Sequence[ContextCandidate]
    weight: float = 1.0


def _identity(candidate: ContextCandidate) -> tuple[object, ...]:
    if candidate.path:
        return (candidate.path, candidate.start_line, candidate.end_line)
    return (candidate.candidate_id,)


def _representation_rank(value: str) -> int:
    return {
        "BODY": 6,
        "SYMBOL_BODY": 5,
        "TARGETED_SLICE": 4,
        "SLICE": 3,
        "SKELETON": 2,
        "SUMMARY": 1,
    }.get(value, 0)


def _merge(left: ContextCandidate, right: ContextCandidate) -> ContextCandidate:
    preferred = left
    if _representation_rank(right.representation) > _representation_rank(left.representation):
        preferred = right
    elif len(right.content) > len(left.content) and right.estimated_tokens <= left.estimated_tokens * 2:
        preferred = right

    reasons = []
    for reason in (left.selection_reason, right.selection_reason):
        if reason and reason not in reasons:
            reasons.append(reason)
    return replace(
        preferred,
        relevance=max(left.relevance, right.relevance),
        confidence=max(left.confidence, right.confidence),
        freshness=max(left.freshness, right.freshness),
        mandatory=left.mandatory or right.mandatory,
        dependencies=tuple(dict.fromkeys((*left.dependencies, *right.dependencies))),
        selection_reason="; ".join(reasons)[:600],
    )


class ReciprocalRankFusion:
    """Fuse rankers without assuming their raw scores share the same scale."""

    def __init__(self, k: int = 60) -> None:
        self.k = max(1, int(k))

    def fuse(
        self,
        sources: Iterable[RankedSource],
        mandatory: Sequence[ContextCandidate] = (),
    ) -> list[ContextCandidate]:
        representatives: dict[tuple[object, ...], ContextCandidate] = {}
        scores: dict[tuple[object, ...], float] = {}
        source_names: dict[tuple[object, ...], list[str]] = {}

        for candidate in mandatory:
            key = _identity(candidate)
            representatives[key] = (
                _merge(representatives[key], candidate) if key in representatives else candidate
            )

        for source in sources:
            if source.weight <= 0:
                continue
            local_seen: set[tuple[object, ...]] = set()
            for rank, candidate in enumerate(source.candidates, start=1):
                key = _identity(candidate)
                if key in local_seen:
                    continue
                local_seen.add(key)
                if key in representatives:
                    representatives[key] = _merge(representatives[key], candidate)
                else:
                    representatives[key] = candidate
                scores[key] = scores.get(key, 0.0) + source.weight / (self.k + rank)
                source_names.setdefault(key, []).append(source.name)

        max_score = max(scores.values(), default=0.0)
        fused: list[tuple[float, ContextCandidate]] = []
        for key, candidate in representatives.items():
            if candidate.mandatory:
                fused.append((float("inf"), candidate))
                continue
            score = scores.get(key, 0.0)
            normalized = score / max_score if max_score > 0 else 0.0
            reason = candidate.selection_reason
            names = tuple(dict.fromkeys(source_names.get(key, ())))
            if names:
                reason = f"{reason}; RRF={','.join(names)}"
            candidate = replace(
                candidate,
                # Preserve a small amount of deterministic base relevance while
                # making the fused rank the dominant selection signal.
                relevance=max(0.0, min(1.0, 0.30 * candidate.relevance + 0.70 * normalized)),
                selection_reason=reason[:600],
            )
            fused.append((score, candidate))

        fused.sort(key=lambda item: (not item[1].mandatory, -item[0] if item[0] != float("inf") else 0))
        # Mandatory entries have already been forced to the front; sort the rest
        # explicitly because `inf` handling above is intentionally simple.
        mandatory_items = [candidate for _, candidate in fused if candidate.mandatory]
        regular_items = sorted(
            ((score, candidate) for score, candidate in fused if not candidate.mandatory),
            key=lambda item: item[0],
            reverse=True,
        )
        return [*mandatory_items, *(candidate for _, candidate in regular_items)]
