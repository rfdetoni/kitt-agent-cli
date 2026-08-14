"""Context candidate definition and knapsack value/token selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple, List, Optional


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
        return (self.relevance * 0.6 + self.confidence * 0.3 + self.freshness * 0.1)


class ContextSelector:
    """Greedy value/token knapsack selector enforcing mandatory context and token budgets."""

    @staticmethod
    def _terms(candidate: ContextCandidate) -> set[str]:
        text = f"{candidate.path or ''}\n{candidate.content}"
        return set(re.findall(r"[A-Za-z0-9_]{3,}", text.lower()))

    @classmethod
    def _too_redundant(cls, candidate: ContextCandidate, selected: List[ContextCandidate]) -> bool:
        cand_terms = cls._terms(candidate)
        for chosen in selected:
            if candidate.content_hash and candidate.content_hash == chosen.content_hash:
                return True
            if candidate.path == chosen.path and candidate.start_line == chosen.start_line and candidate.end_line == chosen.end_line:
                return True
            chosen_terms = cls._terms(chosen)
            if cand_terms and chosen_terms and len(cand_terms & chosen_terms) / len(cand_terms | chosen_terms) >= 0.85:
                return True
        return False

    @staticmethod
    def select_candidates(
        candidates: List[ContextCandidate],
        max_token_budget: int
    ) -> Tuple[List[ContextCandidate], List[ContextCandidate]]:
        selected: List[ContextCandidate] = []
        discarded: List[ContextCandidate] = []
        spent_tokens = 0
        selected_ids = set()
        by_id = {cand.candidate_id: cand for cand in candidates}

        # 1. Select mandatory items first
        for cand in candidates:
            if cand.mandatory:
                if spent_tokens + cand.estimated_tokens <= max_token_budget:
                    selected.append(cand)
                    selected_ids.add(cand.candidate_id)
                    spent_tokens += cand.estimated_tokens
                    for dep_id in cand.dependencies:
                        dep = by_id.get(dep_id)
                        if dep and dep.candidate_id not in selected_ids:
                            if spent_tokens + dep.estimated_tokens <= max_token_budget:
                                selected.append(dep)
                                selected_ids.add(dep.candidate_id)
                                spent_tokens += dep.estimated_tokens
                            else:
                                discarded.append(dep)
                else:
                    discarded.append(cand)

        # 2. Sort remaining items by marginal_value / estimated_tokens
        remaining = [c for c in candidates if not c.mandatory and c.candidate_id not in selected_ids]
        remaining.sort(key=lambda c: (c.marginal_value / max(1, c.estimated_tokens)), reverse=True)

        # 3. Greedy selection with cheap MMR-style de-duplication by same range.
        for cand in remaining:
            if ContextSelector._too_redundant(cand, selected):
                discarded.append(cand)
                continue
            if spent_tokens + cand.estimated_tokens <= max_token_budget:
                selected.append(cand)
                selected_ids.add(cand.candidate_id)
                spent_tokens += cand.estimated_tokens
                for dep_id in cand.dependencies:
                    dep = by_id.get(dep_id)
                    if dep and dep.candidate_id not in selected_ids:
                        if spent_tokens + dep.estimated_tokens <= max_token_budget:
                            selected.append(dep)
                            selected_ids.add(dep.candidate_id)
                            spent_tokens += dep.estimated_tokens
                        else:
                            discarded.append(dep)
            else:
                discarded.append(cand)

        return selected, discarded
