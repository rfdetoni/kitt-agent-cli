"""Reciprocal Rank Fusion (RRF) for heterogeneous code retrieval signals."""
from __future__ import annotations

import math
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


def _range_overlap_ratio(left: ContextCandidate, right: ContextCandidate) -> float:
    if not left.path or left.path != right.path:
        return 0.0
    if None in (left.start_line, left.end_line, right.start_line, right.end_line):
        return 0.0
    left_start, left_end = int(left.start_line), int(left.end_line)
    right_start, right_end = int(right.start_line), int(right.end_line)
    if left_end < left_start or right_end < right_start:
        return 0.0
    overlap = max(0, min(left_end, right_end) - max(left_start, right_start) + 1)
    if overlap <= 0:
        return 0.0
    smaller = min(left_end - left_start + 1, right_end - right_start + 1)
    return overlap / max(1, smaller)


def _same_evidence(left: ContextCandidate, right: ContextCandidate) -> bool:
    if left.content_hash and left.content_hash == right.content_hash:
        return True
    return _range_overlap_ratio(left, right) >= 0.65


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

    reasons: list[str] = []
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


def _resolve_key(
    candidate: ContextCandidate,
    representatives: dict[tuple[object, ...], ContextCandidate],
) -> tuple[object, ...]:
    for key, existing in representatives.items():
        if _same_evidence(existing, candidate):
            return key
    return _identity(candidate)


class ReciprocalRankFusion:
    """Fuse rankers without assuming raw scores share the same scale."""

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
            key = _resolve_key(candidate, representatives)
            representatives[key] = (
                _merge(representatives[key], candidate) if key in representatives else candidate
            )

        for source in sources:
            weight = float(source.weight)
            if weight <= 0 or not math.isfinite(weight):
                continue
            local_seen: set[tuple[object, ...]] = set()
            for rank, candidate in enumerate(source.candidates, start=1):
                key = _resolve_key(candidate, representatives)
                if key in local_seen:
                    continue
                local_seen.add(key)
                representatives[key] = (
                    _merge(representatives[key], candidate)
                    if key in representatives
                    else candidate
                )
                scores[key] = scores.get(key, 0.0) + weight / (self.k + rank)
                source_names.setdefault(key, []).append(source.name)

        max_score = max((score for score in scores.values() if math.isfinite(score)), default=0.0)
        mandatory_items: list[ContextCandidate] = []
        regular_items: list[tuple[float, ContextCandidate]] = []
        for key, candidate in representatives.items():
            if candidate.mandatory:
                mandatory_items.append(candidate)
                continue
            score = scores.get(key, 0.0)
            if not math.isfinite(score):
                score = 0.0
            normalized = score / max_score if max_score > 0 else 0.0
            reason = candidate.selection_reason
            names = tuple(dict.fromkeys(source_names.get(key, ())))
            if names:
                reason = f"{reason}; RRF={','.join(names)}"
            candidate = replace(
                candidate,
                relevance=max(0.0, min(1.0, 0.30 * candidate.relevance + 0.70 * normalized)),
                selection_reason=reason[:600],
            )
            regular_items.append((score, candidate))

        regular_items.sort(key=lambda item: item[0], reverse=True)
        return [*mandatory_items, *(candidate for _, candidate in regular_items)]
