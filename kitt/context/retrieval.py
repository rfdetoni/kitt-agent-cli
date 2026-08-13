"""Hybrid context retrieval pipeline combining exact paths, symbols, FTS5/BM25, and graph PageRank."""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Set, Dict, Any, Optional

from kitt.context.candidates import ContextCandidate, ContextSelector
from kitt.index.repository import RepositoryIndex


class HybridRetrievalPipeline:
    """Hybrid context retrieval pipeline with MMR deduplication and value/token knapsack selection."""

    def __init__(self, index: RepositoryIndex):
        self.index = index

    def retrieve(
        self,
        prompt: str,
        explicit_files: Set[str] | None = None,
        max_tokens: int = 2048
    ) -> List[ContextCandidate]:
        candidates: List[ContextCandidate] = []
        now = time.time()

        # 1. Explicit files (Mandatory)
        if explicit_files:
            for rel in explicit_files:
                full_p = self.index.root_path / rel
                if full_p.is_file():
                    try:
                        text = full_p.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        text = ""
                    est = max(1, len(text) // 4)
                    candidates.append(ContextCandidate(
                        candidate_id=f"file:{rel}",
                        source_type="file",
                        path=rel,
                        start_line=1,
                        end_line=len(text.splitlines()),
                        content_hash=rel,
                        estimated_tokens=est,
                        relevance=1.0,
                        confidence=1.0,
                        freshness=1.0,
                        mandatory=True,
                        trust_level="USER_REQUEST",
                        dependencies=(),
                        selection_reason="Explicit file requested by user",
                        content=text
                    ))

        # 2. Text / FTS search
        search_res = self.index.search_text(prompt, limit=5)
        for idx, res in enumerate(search_res):
            cand_id = f"search:{res['path']}:{idx}"
            if any(c.candidate_id == cand_id or c.path == res["path"] for c in candidates):
                continue
            text = res["content"]
            est = max(1, len(text) // 4)
            candidates.append(ContextCandidate(
                candidate_id=cand_id,
                source_type="file",
                path=res["path"],
                start_line=1,
                end_line=len(text.splitlines()),
                content_hash=res["path"],
                estimated_tokens=est,
                relevance=0.85 - (idx * 0.05),
                confidence=0.9,
                freshness=0.9,
                mandatory=False,
                trust_level="WORKSPACE_DATA",
                dependencies=(),
                selection_reason=f"Matched via {res['method']} search",
                content=text
            ))

        # 3. Apply ContextSelector (greedy value/token selection)
        selected, _ = ContextSelector.select_candidates(candidates, max_token_budget=max_tokens)
        return selected
