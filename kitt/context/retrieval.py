"""Hybrid context retrieval pipeline combining exact paths, symbols, FTS5/BM25, and graph PageRank."""

from __future__ import annotations

import time
import hashlib
import subprocess
from pathlib import Path
from typing import List, Set, Dict, Any, Optional

from kitt.context.candidates import ContextCandidate, ContextSelector
from kitt.context.query_plan import QueryPlan, QueryPlanner
from kitt.index.repository import RepositoryIndex


class HybridRetrievalPipeline:
    """Hybrid context retrieval pipeline with MMR deduplication and value/token knapsack selection."""

    def __init__(self, index: RepositoryIndex):
        self.index = index

    def retrieve(
        self,
        prompt: str,
        explicit_files: Set[str] | None = None,
        max_tokens: int = 2048,
        working_set_paths: Set[str] | None = None,
    ) -> List[ContextCandidate]:
        selected, _discarded, _plan = self.retrieve_with_rejections(prompt, explicit_files, max_tokens, working_set_paths=working_set_paths)
        return selected

    def retrieve_with_rejections(
        self,
        prompt: str,
        explicit_files: Set[str] | None = None,
        max_tokens: int = 2048,
        plan: QueryPlan | None = None,
        working_set_paths: Set[str] | None = None,
    ) -> tuple[List[ContextCandidate], List[ContextCandidate], QueryPlan]:
        candidates: List[ContextCandidate] = []
        now = time.time()
        plan = plan or QueryPlanner.plan(prompt, explicit_files=explicit_files or (), token_budget=max_tokens)
        working_set_paths = set(working_set_paths or ())

        # 1. Explicit files (Mandatory)
        if plan.exact_paths:
            for rel in plan.exact_paths:
                full_p = self.index.root_path / rel
                if full_p.is_file():
                    try:
                        text = full_p.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        text = ""
                    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    est = max(1, len(text) // 4)
                    representation = "BODY"
                    reason = "Explicit file requested by user"
                    if est > max_tokens:
                        suffix = "\n... [truncated explicit file]"
                        text = text[: max(0, max_tokens * 4 - len(suffix))] + suffix
                        est = max(1, len(text) // 4)
                        representation = "TARGETED_SLICE"
                        reason = "Explicit file requested by user; truncated to fit budget"
                    candidates.append(ContextCandidate(
                        candidate_id=f"file:{rel}",
                        source_type="file",
                        path=rel,
                        start_line=1,
                        end_line=len(text.splitlines()),
                        content_hash=digest,
                        estimated_tokens=est,
                        relevance=1.0,
                        confidence=1.0,
                        freshness=1.0,
                        mandatory=True,
                        trust_level="WORKSPACE_DATA",
                        dependencies=(),
                        selection_reason=reason,
                        representation=representation,
                        content=text
                    ))

        # 2. Exact symbol definitions
        for symbol in plan.exact_symbols:
            for idx, res in enumerate(self.index.search_symbol(symbol, limit=plan.candidate_limit)):
                cand_id = f"symbol:{symbol}:{res['path']}:{idx}"
                if any(c.path == res["path"] and c.start_line == res["start_line"] for c in candidates):
                    continue
                text = res["content"]
                candidates.append(ContextCandidate(
                    candidate_id=cand_id,
                    source_type="symbol",
                    path=res["path"],
                    start_line=res["start_line"],
                    end_line=res["end_line"],
                    content_hash=res["content_hash"],
                    estimated_tokens=max(1, len(text) // 4),
                    relevance=0.95,
                    confidence=1.0,
                    freshness=1.0,
                    mandatory=False,
                    trust_level="WORKSPACE_DATA",
                    dependencies=(),
                    selection_reason=f"Exact symbol match: {symbol}",
                    representation="SYMBOL_BODY",
                    content=text,
                ))

        # 3. Text / FTS search
        search_query = " ".join((*plan.exact_symbols, *plan.lexical_terms, *plan.diagnostics)) or prompt
        search_res = self.index.search_text(search_query, limit=plan.candidate_limit)
        for idx, res in enumerate(search_res):
            cand_id = f"search:{res['path']}:{idx}"
            if any(c.candidate_id == cand_id or (c.path == res["path"] and c.start_line == res.get("start_line")) for c in candidates):
                continue
            text = res["content"]
            est = max(1, len(text) // 4)
            in_working_set = res["path"] in working_set_paths
            candidates.append(ContextCandidate(
                candidate_id=cand_id,
                source_type="file",
                path=res["path"],
                start_line=res.get("start_line") or 1,
                end_line=res.get("end_line") or len(text.splitlines()),
                content_hash=res.get("content_hash") or hashlib.sha256(text.encode("utf-8")).hexdigest(),
                estimated_tokens=est,
                relevance=(0.95 if in_working_set else 0.85) - (idx * 0.05),
                confidence=0.9,
                freshness=0.9,
                mandatory=False,
                trust_level="WORKSPACE_DATA",
                dependencies=(),
                selection_reason=f"Matched via {res['method']} search" + ("; working set boost" if in_working_set else ""),
                representation="SLICE",
                content=text
            ))

        for rel in sorted(working_set_paths):
            if any(c.path == rel for c in candidates):
                continue
            row = self._first_chunk(rel)
            if not row:
                continue
            text = row["content"]
            candidates.append(ContextCandidate(
                candidate_id=f"working:{rel}",
                source_type="file",
                path=row["path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                content_hash=row["content_hash"],
                estimated_tokens=max(1, len(text) // 4),
                relevance=0.65,
                confidence=0.8,
                freshness=1.0,
                mandatory=False,
                trust_level="WORKSPACE_DATA",
                dependencies=(),
                selection_reason="Recent conversation working set",
                representation="SKELETON",
                content=text,
            ))

        if self._wants_git_focus(prompt):
            for path in self._git_focus_paths():
                if any(c.path == path for c in candidates):
                    continue
                row = self._first_chunk(path)
                if not row:
                    continue
                text = row["content"]
                candidates.append(ContextCandidate(
                    candidate_id=f"git:{path}",
                    source_type="file",
                    path=row["path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    content_hash=row["content_hash"],
                    estimated_tokens=max(1, len(text) // 4),
                    relevance=0.75,
                    confidence=0.85,
                    freshness=1.0,
                    mandatory=False,
                    trust_level="WORKSPACE_DATA",
                    dependencies=(),
                    selection_reason="Git status focus",
                    representation="SKELETON",
                    content=text,
                ))

        # Project-overview fallback: bounded catalog slices, not whole repo.
        if not candidates and any(term in prompt.lower() for term in ("projeto", "project", "repo", "repository")):
            with self.index._lock:
                rows = self.index._conn.execute(
                    """
                    SELECT f.path, c.content, c.start_line, c.end_line, c.content_hash
                    FROM files f
                    JOIN chunks c ON c.file_id = f.file_id
                    ORDER BY
                        CASE
                            WHEN lower(f.path) IN ('readme.md', 'readme.txt') THEN 0
                            WHEN lower(f.path) LIKE '%.py' THEN 1
                            ELSE 2
                        END,
                        f.path,
                        c.start_line
                    LIMIT ?
                    """,
                    (min(8, plan.candidate_limit),),
                ).fetchall()
            for idx, row in enumerate(rows):
                text = row["content"]
                candidates.append(ContextCandidate(
                    candidate_id=f"catalog:{row['path']}:{idx}",
                    source_type="file",
                    path=row["path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    content_hash=row["content_hash"],
                    estimated_tokens=max(1, len(text) // 4),
                    relevance=0.45,
                    confidence=0.65,
                    freshness=0.9,
                    mandatory=False,
                    trust_level="WORKSPACE_DATA",
                    dependencies=(),
                    selection_reason="Bounded project overview fallback",
                    representation="SKELETON",
                    content=text,
                ))

        if plan.include_tests:
            for source_path in sorted({c.path for c in candidates if c.path}):
                for test_path in self._paired_test_paths(source_path):
                    if any(c.path == test_path for c in candidates):
                        continue
                    row = self._first_chunk(test_path)
                    if not row:
                        continue
                    text = row["content"]
                    candidates.append(ContextCandidate(
                        candidate_id=f"test:{test_path}",
                        source_type="file",
                        path=row["path"],
                        start_line=row["start_line"],
                        end_line=row["end_line"],
                        content_hash=row["content_hash"],
                        estimated_tokens=max(1, len(text) // 4),
                        relevance=0.7,
                        confidence=0.85,
                        freshness=0.9,
                        mandatory=False,
                        trust_level="WORKSPACE_DATA",
                        dependencies=(),
                        selection_reason=f"Source-test association for {source_path}",
                        representation="SKELETON",
                        content=text,
                    ))

        # 4. Bounded graph expansion: include direct dependencies/dependents from indexed refs.
        seed_paths = {c.path for c in candidates if c.path}
        if seed_paths and (plan.include_dependencies or plan.include_dependents):
            expanded = self.index.graph.expand_neighborhood(set(seed_paths), max_hops=plan.graph_hops, max_nodes=25)
            for path in sorted(expanded - seed_paths):
                row = self._first_chunk(path)
                if not row:
                    continue
                text = row["content"]
                candidates.append(ContextCandidate(
                    candidate_id=f"graph:{path}",
                    source_type="file",
                    path=row["path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    content_hash=row["content_hash"],
                    estimated_tokens=max(1, len(text) // 4),
                    relevance=0.55,
                    confidence=0.75,
                    freshness=0.9,
                    mandatory=False,
                    trust_level="WORKSPACE_DATA",
                    dependencies=(),
                    selection_reason="Graph neighbor of selected candidate",
                    representation="SKELETON",
                    content=text,
                ))

        # 5. Apply ContextSelector (greedy value/token selection)
        selected, discarded = ContextSelector.select_candidates(candidates, max_token_budget=max_tokens)
        return selected, discarded, plan

    def _first_chunk(self, path: str):
        with self.index._lock:
            return self.index._conn.execute(
                """
                SELECT f.path, c.content, c.start_line, c.end_line, c.content_hash
                FROM files f
                JOIN chunks c ON c.file_id = f.file_id
                WHERE f.path = ?
                ORDER BY c.start_line
                LIMIT 1
                """,
                (path,),
            ).fetchone()

    def _paired_test_paths(self, source_path: str) -> List[str]:
        path = Path(source_path)
        stem, suffix = path.stem, path.suffix
        candidates = {
            str(path.with_name(f"test_{stem}{suffix}")),
            str(path.with_name(f"{stem}_test{suffix}")),
            f"tests/test_{stem}{suffix}",
            f"tests/{stem}_test{suffix}",
        }
        with self.index._lock:
            rows = self.index._conn.execute(
                "SELECT path FROM files WHERE path IN (%s) ORDER BY path" % ",".join("?" for _ in candidates),
                tuple(candidates),
            ).fetchall()
        return [row["path"] for row in rows]

    @staticmethod
    def _wants_git_focus(prompt: str) -> bool:
        prompt_l = prompt.lower()
        return any(term in prompt_l for term in ("git", "diff", "alterado", "alterados", "mudança", "mudanças", "changed"))

    def _git_focus_paths(self, limit: int = 20) -> List[str]:
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.index.root_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
        except Exception:
            return []
        if res.returncode != 0:
            return []
        paths = []
        for line in res.stdout.splitlines():
            if not line or len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path and not path.startswith(".kitt/"):
                paths.append(path)
            if len(paths) >= limit:
                break
        return paths
