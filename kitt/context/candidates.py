"""Context candidate definition and knapsack value/token selection."""

from __future__ import annotations

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
    content: str = ""

    @property
    def marginal_value(self) -> float:
        return (self.relevance * 0.6 + self.confidence * 0.3 + self.freshness * 0.1)


class ContextSelector:
    """Greedy value/token knapsack selector enforcing mandatory context and token budgets."""

    @staticmethod
    def select_candidates(
        candidates: List[ContextCandidate],
        max_token_budget: int
    ) -> Tuple[List[ContextCandidate], List[ContextCandidate]]:
        selected: List[ContextCandidate] = []
        discarded: List[ContextCandidate] = []
        spent_tokens = 0
        selected_ids = set()

        # 1. Select mandatory items first
        for cand in candidates:
            if cand.mandatory:
                if spent_tokens + cand.estimated_tokens <= max_token_budget:
                    selected.append(cand)
                    selected_ids.add(cand.candidate_id)
                    spent_tokens += cand.estimated_tokens
                else:
                    discarded.append(cand)

        # 2. Sort remaining items by marginal_value / estimated_tokens
        remaining = [c for c in candidates if not c.mandatory and c.candidate_id not in selected_ids]
        remaining.sort(key=lambda c: (c.marginal_value / max(1, c.estimated_tokens)), reverse=True)

        # 3. Greedy selection
        for cand in remaining:
            if spent_tokens + cand.estimated_tokens <= max_token_budget:
                selected.append(cand)
                selected_ids.add(cand.candidate_id)
                spent_tokens += cand.estimated_tokens
            else:
                discarded.append(cand)

        return selected, discarded
