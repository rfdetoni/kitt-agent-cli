"""Context candidate definition and bounded value/token selection."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class ContextCandidate:
    candidate_id: str
    source_type: str  # file|symbol|doc|summary|result
    path: Optional[str]
    start_line: Optional[int]
    end_line: Optional[int]
    content_hash: str
    estimated_tokens: int
    relevance: float
    confidence: float
    freshness: float
    mandatory: bool
    trust_level: str  # SYSTEM_POLICY|USER_REQUEST|WORKSPACE_DATA|TOOL_OUTPUT|REMOTE_CONTENT
    dependencies: Tuple[str, ...]
    selection_reason: str
    representation: str = "SLICE"
    content: str = ""

    @property
    def marginal_value(self) -> float:
        return self.relevance * 0.6 + self.confidence * 0.3 + self.freshness * 0.1


class ContextSelector:
    """Greedy selector with mandatory context, budgets and overlap-aware MMR."""

    @staticmethod
    def _terms(candidate: ContextCandidate) -> set[str]:
        text = f"{candidate.path or ''}\n{candidate.content}"
        return set(re.findall(r"[A-Za-z0-9_]{3,}", text.lower()))

    @staticmethod
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

    @classmethod
    def _too_redundant(
        cls,
        candidate: ContextCandidate,
        selected: List[ContextCandidate],
    ) -> bool:
        cand_terms = cls._terms(candidate)
        for chosen in selected:
            if candidate.content_hash and candidate.content_hash == chosen.content_hash:
                return True
            if cls._range_overlap_ratio(candidate, chosen) >= 0.80:
                return True
            chosen_terms = cls._terms(chosen)
            if cand_terms and chosen_terms:
                union = cand_terms | chosen_terms
                if union and len(cand_terms & chosen_terms) / len(union) >= 0.85:
                    return True
        return False

    @staticmethod
    def select_candidates(
        candidates: List[ContextCandidate],
        max_token_budget: int,
    ) -> Tuple[List[ContextCandidate], List[ContextCandidate]]:
        budget = max(0, int(max_token_budget))
        selected: List[ContextCandidate] = []
        discarded: List[ContextCandidate] = []
        spent_tokens = 0
        selected_ids: set[str] = set()
        by_id = {cand.candidate_id: cand for cand in candidates}

        def try_add(candidate: ContextCandidate, *, check_redundancy: bool) -> bool:
            nonlocal spent_tokens
            if candidate.candidate_id in selected_ids:
                return True
            if check_redundancy and ContextSelector._too_redundant(candidate, selected):
                discarded.append(candidate)
                return False
            cost = max(1, int(candidate.estimated_tokens))
            if spent_tokens + cost > budget:
                discarded.append(candidate)
                return False
            selected.append(candidate)
            selected_ids.add(candidate.candidate_id)
            spent_tokens += cost
            return True

        def add_dependencies(candidate: ContextCandidate) -> None:
            for dep_id in candidate.dependencies:
                dep = by_id.get(dep_id)
                if dep is not None and dep.candidate_id not in selected_ids:
                    try_add(dep, check_redundancy=True)

        for cand in candidates:
            if cand.mandatory and try_add(cand, check_redundancy=False):
                add_dependencies(cand)

        remaining = [
            candidate
            for candidate in candidates
            if not candidate.mandatory and candidate.candidate_id not in selected_ids
        ]
        remaining.sort(
            key=lambda candidate: (
                candidate.marginal_value / max(1, candidate.estimated_tokens),
                candidate.marginal_value,
            ),
            reverse=True,
        )

        for cand in remaining:
            if try_add(cand, check_redundancy=True):
                add_dependencies(cand)

        return selected, discarded
