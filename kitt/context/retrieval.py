"""Hybrid Code-RAG retrieval with native Rust lexical discovery and bounded semantics."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import List, Set

from kitt.context.candidates import ContextCandidate, ContextSelector
from kitt.context.query_plan import QueryPlan, QueryPlanner
from kitt.context.rag.config import RagConfig
from kitt.context.rag.embeddings import EmbeddingProvider, OllamaEmbeddingProvider
from kitt.context.rag.fusion import RankedSource, ReciprocalRankFusion
from kitt.context.rag.native_search import NativeLexicalRetriever
from kitt.context.rag.semantic import SemanticCandidateReranker
from kitt.index.repository import RepositoryIndex
from kitt.native.bridge import NativeCodeEngine
from kitt.security.workspace_fs import WorkspaceFileSystem


class HybridRetrievalPipeline:
    """Native-first hybrid retrieval for small-context coding models.

    Repository discovery remains deterministic:
      * explicit workspace paths are authoritative;
      * symbols come from the incremental repository index;
      * lexical file search is delegated to KITT's Rust engine when available;
      * indexed FTS is the fallback when the Rust extension is unavailable;
      * tests, Git working-set and dependency graph remain structural signals.

    Optional embeddings never scan the repository and never replace those
    mechanisms. They rerank only the bounded candidate pool before RRF fusion.
    """

    def __init__(
        self,
        index: RepositoryIndex,
        *,
        native_engine: NativeCodeEngine | None = None,
        rag_config: RagConfig | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        self.index = index
        self.workspace_fs = WorkspaceFileSystem(
            index.root_path,
            max_file_bytes=max(index.max_file_bytes, 8 * 1024 * 1024),
        )
        self.rag_config = rag_config or RagConfig.from_env()
        self.native_engine = native_engine or NativeCodeEngine(str(index.root_path))
        self.native_retriever = NativeLexicalRetriever(self.native_engine)
        self.embedding_provider = embedding_provider or self._build_embedding_provider()
        self.last_retrieval_stats: dict[str, object] = {}

    def _build_embedding_provider(self) -> EmbeddingProvider | None:
        config = self.rag_config
        if not config.semantic_enabled:
            return None
        if config.embedding_provider != "ollama":
            return None
        try:
            return OllamaEmbeddingProvider(
                model=config.embedding_model,
                base_url=config.ollama_base_url,
                timeout_seconds=config.embedding_timeout_seconds,
                batch_size=config.embedding_batch_size,
            )
        except (TypeError, ValueError):
            return None

    def retrieve(
        self,
        prompt: str,
        explicit_files: Set[str] | None = None,
        max_tokens: int = 2048,
        working_set_paths: Set[str] | None = None,
    ) -> List[ContextCandidate]:
        selected, _discarded, _plan = self.retrieve_with_rejections(
            prompt,
            explicit_files,
            max_tokens,
            working_set_paths=working_set_paths,
        )
        return selected

    def retrieve_with_rejections(
        self,
        prompt: str,
        explicit_files: Set[str] | None = None,
        max_tokens: int = 2048,
        plan: QueryPlan | None = None,
        working_set_paths: Set[str] | None = None,
    ) -> tuple[List[ContextCandidate], List[ContextCandidate], QueryPlan]:
        plan = plan or QueryPlanner.plan(
            prompt,
            explicit_files=explicit_files or (),
            token_budget=max_tokens,
        )
        working_set_paths = set(working_set_paths or ())
        mandatory = self._explicit_path_candidates(plan, max_tokens)
        sources: list[RankedSource] = []

        symbol_candidates = self._symbol_candidates(plan)
        if symbol_candidates:
            sources.append(
                RankedSource("symbol", symbol_candidates, self.rag_config.symbol_weight)
            )

        native_candidates = self.native_retriever.retrieve(
            prompt,
            plan,
            working_set_paths=working_set_paths,
            max_results=min(
                max(plan.candidate_limit, self.rag_config.native_candidate_limit), 128
            ),
            context_lines=self.rag_config.native_context_lines,
            token_budget=min(
                max(256, self.rag_config.native_token_budget),
                max(256, max_tokens),
            ),
        )
        lexical_backend = "rust"
        if not native_candidates:
            # Do not invoke NativeCodeEngine's Python file-scanner fallback.
            # The existing indexed FTS is cheaper and avoids duplicate scans.
            native_candidates = self._indexed_lexical_candidates(
                prompt, plan, working_set_paths
            )
            lexical_backend = "indexed_fts"
        if native_candidates:
            sources.append(
                RankedSource("lexical", native_candidates, self.rag_config.lexical_weight)
            )

        working_candidates = self._working_set_candidates(
            working_set_paths, already=self._paths_from(mandatory, symbol_candidates, native_candidates)
        )
        if working_candidates:
            sources.append(
                RankedSource(
                    "working_set", working_candidates, self.rag_config.working_set_weight
                )
            )

        git_candidates: list[ContextCandidate] = []
        if self._wants_git_focus(prompt):
            git_candidates = self._git_candidates(
                already=self._paths_from(
                    mandatory, symbol_candidates, native_candidates, working_candidates
                )
            )
            if git_candidates:
                sources.append(
                    RankedSource("git", git_candidates, self.rag_config.git_weight)
                )

        deterministic_pool = [
            *mandatory,
            *symbol_candidates,
            *native_candidates,
            *working_candidates,
            *git_candidates,
        ]

        if not deterministic_pool and self._wants_project_overview(prompt):
            catalog = self._catalog_candidates(plan)
            if catalog:
                sources.append(RankedSource("catalog", catalog, 0.60))
                deterministic_pool.extend(catalog)

        test_candidates: list[ContextCandidate] = []
        if plan.include_tests:
            test_candidates = self._test_candidates(deterministic_pool)
            if test_candidates:
                sources.append(
                    RankedSource("tests", test_candidates, self.rag_config.test_weight)
                )
                deterministic_pool.extend(test_candidates)

        graph_candidates: list[ContextCandidate] = []
        seed_paths = {candidate.path for candidate in deterministic_pool if candidate.path}
        if seed_paths and (plan.include_dependencies or plan.include_dependents):
            graph_candidates = self._graph_candidates(seed_paths, plan)
            if graph_candidates:
                sources.append(
                    RankedSource("graph", graph_candidates, self.rag_config.graph_weight)
                )
                deterministic_pool.extend(graph_candidates)

        # If deterministic retrieval is sparse, embeddings may otherwise have
        # nothing useful to rerank. Build a bounded, structurally-diverse seed
        # pool from the *existing SQLite index* (never from another file scan).
        semantic_seed_candidates: list[ContextCandidate] = []
        non_mandatory_count = sum(1 for candidate in deterministic_pool if not candidate.mandatory)
        if self.embedding_provider is not None and non_mandatory_count < 4:
            semantic_seed_candidates = self._semantic_seed_candidates(
                already={candidate.path for candidate in deterministic_pool if candidate.path},
                limit=min(
                    96,
                    max(12, self.rag_config.semantic_candidate_limit * 2),
                ),
            )
            if semantic_seed_candidates:
                sources.append(RankedSource("semantic_seed", semantic_seed_candidates, 0.35))
                deterministic_pool.extend(semantic_seed_candidates)

        # Fuse deterministic signals once before semantics. This gives the
        # embedding stage a bounded, de-duplicated and multi-signal candidate
        # pool instead of simply taking the first N lexical hits.
        rrf = ReciprocalRankFusion(self.rag_config.rrf_k)
        deterministic_fused = rrf.fuse(sources, mandatory=mandatory)

        semantic_degraded = False
        semantic_reason = "disabled"
        semantic_count = 0
        if self.embedding_provider is not None and deterministic_fused:
            semantic = SemanticCandidateReranker(
                self.embedding_provider,
                max_candidates=self.rag_config.semantic_candidate_limit,
                max_chars=self.rag_config.max_embedding_chars,
            ).rank(prompt, deterministic_fused)
            semantic_degraded = semantic.degraded
            semantic_reason = semantic.reason or "ok"
            semantic_count = len(semantic.candidates)
            if semantic.candidates:
                sources.append(
                    RankedSource(
                        "semantic",
                        semantic.candidates,
                        self.rag_config.semantic_weight,
                    )
                )

        fused = rrf.fuse(sources, mandatory=mandatory)
        selected, discarded = ContextSelector.select_candidates(
            fused, max_token_budget=max_tokens
        )
        self.last_retrieval_stats = {
            "native_backend": getattr(self.native_engine.status, "backend", "unknown"),
            "native_available": bool(getattr(self.native_engine.status, "available", False)),
            "lexical_backend": lexical_backend,
            "symbol_candidates": len(symbol_candidates),
            "lexical_candidates": len(native_candidates),
            "working_set_candidates": len(working_candidates),
            "git_candidates": len(git_candidates),
            "test_candidates": len(test_candidates),
            "graph_candidates": len(graph_candidates),
            "semantic_seed_candidates": len(semantic_seed_candidates),
            "semantic_enabled": self.embedding_provider is not None,
            "semantic_candidates": semantic_count,
            "semantic_degraded": semantic_degraded,
            "semantic_reason": semantic_reason,
            "deterministic_fused_candidates": len(deterministic_fused),
            "fused_candidates": len(fused),
            "selected": len(selected),
            "discarded": len(discarded),
        }
        return selected, discarded, plan

    def _explicit_path_candidates(
        self, plan: QueryPlan, max_tokens: int
    ) -> list[ContextCandidate]:
        candidates: list[ContextCandidate] = []
        for requested in plan.exact_paths:
            try:
                rel = self.workspace_fs.relative(requested)
                if rel == ".":
                    continue
                char_limit = max(1, max_tokens * 4)
                data = self.workspace_fs.read_prefix(
                    rel,
                    max_bytes=min(self.workspace_fs.max_file_bytes, char_limit + 4),
                )
                text = data.content.decode("utf-8", errors="ignore")
            except (FileNotFoundError, IsADirectoryError, PermissionError, ValueError, OSError):
                continue

            digest = self._indexed_hash(rel) or hashlib.sha256(
                text.encode("utf-8")
            ).hexdigest()
            representation = "BODY"
            reason = "Explicit file requested by user"
            if not data.complete or len(text) > char_limit:
                suffix = "\n... [truncated explicit file]"
                text = text[: max(0, char_limit - len(suffix))] + suffix
                representation = "TARGETED_SLICE"
                reason = "Explicit file requested by user; truncated to fit budget"
            candidates.append(
                ContextCandidate(
                    candidate_id=f"file:{rel}",
                    source_type="file",
                    path=rel,
                    start_line=1,
                    end_line=len(text.splitlines()),
                    content_hash=digest,
                    estimated_tokens=max(1, len(text) // 4),
                    relevance=1.0,
                    confidence=1.0,
                    freshness=1.0,
                    mandatory=True,
                    trust_level="WORKSPACE_DATA",
                    dependencies=(),
                    selection_reason=reason,
                    representation=representation,
                    content=text,
                )
            )
        return candidates

    def _symbol_candidates(self, plan: QueryPlan) -> list[ContextCandidate]:
        candidates: list[ContextCandidate] = []
        seen: set[tuple[object, ...]] = set()
        for symbol in plan.exact_symbols:
            try:
                results = self.index.search_symbol(symbol, limit=plan.candidate_limit)
            except (OSError, RuntimeError, ValueError):
                continue
            for idx, result in enumerate(results):
                key = (result["path"], result["start_line"], result["end_line"])
                if key in seen:
                    continue
                seen.add(key)
                text = result["content"]
                candidates.append(
                    ContextCandidate(
                        candidate_id=f"symbol:{symbol}:{result['path']}:{idx}",
                        source_type="symbol",
                        path=result["path"],
                        start_line=result["start_line"],
                        end_line=result["end_line"],
                        content_hash=result["content_hash"],
                        estimated_tokens=max(1, len(text) // 4),
                        relevance=max(0.55, 0.96 - idx * 0.03),
                        confidence=1.0,
                        freshness=1.0,
                        mandatory=False,
                        trust_level="WORKSPACE_DATA",
                        dependencies=(),
                        selection_reason=f"Exact symbol match: {symbol}",
                        representation="SYMBOL_BODY",
                        content=text,
                    )
                )
        return candidates

    def _indexed_lexical_candidates(
        self,
        prompt: str,
        plan: QueryPlan,
        working_set_paths: set[str],
    ) -> list[ContextCandidate]:
        search_query = " ".join(
            (*plan.exact_symbols, *plan.lexical_terms, *plan.diagnostics)
        ) or prompt
        try:
            results = self.index.search_text(search_query, limit=plan.candidate_limit)
        except (OSError, RuntimeError, ValueError):
            return []
        candidates: list[ContextCandidate] = []
        for idx, result in enumerate(results):
            text = result["content"]
            in_working_set = result["path"] in working_set_paths
            candidates.append(
                ContextCandidate(
                    candidate_id=f"fts:{result['path']}:{idx}",
                    source_type="file",
                    path=result["path"],
                    start_line=result.get("start_line") or 1,
                    end_line=result.get("end_line") or len(text.splitlines()),
                    content_hash=result.get("content_hash")
                    or hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    estimated_tokens=max(1, len(text) // 4),
                    relevance=max(
                        0.35,
                        (0.93 if in_working_set else 0.84) - idx * 0.025,
                    ),
                    confidence=0.90,
                    freshness=0.90,
                    mandatory=False,
                    trust_level="WORKSPACE_DATA",
                    dependencies=(),
                    selection_reason=f"Matched via {result['method']} indexed search"
                    + ("; working set boost" if in_working_set else ""),
                    representation="SLICE",
                    content=text,
                )
            )
        return candidates

    def _working_set_candidates(
        self, working_set_paths: set[str], already: set[str]
    ) -> list[ContextCandidate]:
        candidates: list[ContextCandidate] = []
        for raw_rel in sorted(working_set_paths):
            try:
                rel = self.workspace_fs.relative(raw_rel)
            except PermissionError:
                continue
            if rel in already:
                continue
            row = self._first_chunk(rel)
            if not row:
                continue
            text = row["content"]
            candidates.append(
                ContextCandidate(
                    candidate_id=f"working:{rel}",
                    source_type="file",
                    path=row["path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    content_hash=row["content_hash"],
                    estimated_tokens=max(1, len(text) // 4),
                    relevance=0.68,
                    confidence=0.82,
                    freshness=1.0,
                    mandatory=False,
                    trust_level="WORKSPACE_DATA",
                    dependencies=(),
                    selection_reason="Recent conversation working set",
                    representation="SKELETON",
                    content=text,
                )
            )
        return candidates

    def _git_candidates(self, already: set[str]) -> list[ContextCandidate]:
        candidates: list[ContextCandidate] = []
        for path in self._git_focus_paths():
            if path in already:
                continue
            row = self._first_chunk(path)
            if not row:
                continue
            text = row["content"]
            candidates.append(
                ContextCandidate(
                    candidate_id=f"git:{path}",
                    source_type="file",
                    path=row["path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    content_hash=row["content_hash"],
                    estimated_tokens=max(1, len(text) // 4),
                    relevance=0.76,
                    confidence=0.86,
                    freshness=1.0,
                    mandatory=False,
                    trust_level="WORKSPACE_DATA",
                    dependencies=(),
                    selection_reason="Git status focus",
                    representation="SKELETON",
                    content=text,
                )
            )
        return candidates

    def _catalog_candidates(self, plan: QueryPlan) -> list[ContextCandidate]:
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
                (min(12, plan.candidate_limit),),
            ).fetchall()
        return [
            ContextCandidate(
                candidate_id=f"catalog:{row['path']}:{idx}",
                source_type="file",
                path=row["path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                content_hash=row["content_hash"],
                estimated_tokens=max(1, len(row["content"]) // 4),
                relevance=0.45,
                confidence=0.65,
                freshness=0.90,
                mandatory=False,
                trust_level="WORKSPACE_DATA",
                dependencies=(),
                selection_reason="Bounded project overview fallback",
                representation="SKELETON",
                content=row["content"],
            )
            for idx, row in enumerate(rows)
        ]

    def _test_candidates(
        self, candidates: list[ContextCandidate]
    ) -> list[ContextCandidate]:
        result: list[ContextCandidate] = []
        already = {candidate.path for candidate in candidates if candidate.path}
        for source_path in sorted(already):
            for test_path in self._paired_test_paths(source_path):
                if test_path in already:
                    continue
                row = self._first_chunk(test_path)
                if not row:
                    continue
                already.add(test_path)
                text = row["content"]
                result.append(
                    ContextCandidate(
                        candidate_id=f"test:{test_path}",
                        source_type="file",
                        path=row["path"],
                        start_line=row["start_line"],
                        end_line=row["end_line"],
                        content_hash=row["content_hash"],
                        estimated_tokens=max(1, len(text) // 4),
                        relevance=0.70,
                        confidence=0.85,
                        freshness=0.90,
                        mandatory=False,
                        trust_level="WORKSPACE_DATA",
                        dependencies=(),
                        selection_reason=f"Source-test association for {source_path}",
                        representation="SKELETON",
                        content=text,
                    )
                )
        return result

    def _graph_candidates(
        self, seed_paths: set[str], plan: QueryPlan
    ) -> list[ContextCandidate]:
        expanded = self.index.graph.expand_neighborhood(
            set(seed_paths), max_hops=plan.graph_hops, max_nodes=25
        )
        candidates: list[ContextCandidate] = []
        for path in sorted(expanded - seed_paths):
            row = self._first_chunk(path)
            if not row:
                continue
            text = row["content"]
            candidates.append(
                ContextCandidate(
                    candidate_id=f"graph:{path}",
                    source_type="file",
                    path=row["path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    content_hash=row["content_hash"],
                    estimated_tokens=max(1, len(text) // 4),
                    relevance=0.56,
                    confidence=0.76,
                    freshness=0.90,
                    mandatory=False,
                    trust_level="WORKSPACE_DATA",
                    dependencies=(),
                    selection_reason="Dependency graph neighbor of retrieval seed",
                    representation="SKELETON",
                    content=text,
                )
            )
        return candidates

    def _semantic_seed_candidates(
        self, already: set[str], limit: int
    ) -> list[ContextCandidate]:
        """Return diverse indexed chunks for semantic fallback without file I/O.

        The pool is deliberately bounded and takes at most one code-dense chunk
        per file. PageRank-central files are considered first when the graph is
        available, followed by chunks with many indexed symbols.
        """
        limit = max(4, min(96, int(limit)))
        ranked_paths: list[str] = []
        try:
            scores = self.index.graph.cached_scores or self.index.graph.compute_pagerank()
            ranked_paths = [
                path
                for path, _score in sorted(
                    scores.items(), key=lambda item: item[1], reverse=True
                )
                if path not in already
            ][: min(24, limit)]
        except Exception:
            ranked_paths = []

        rows = []
        with self.index._lock:
            if ranked_paths:
                placeholders = ",".join("?" for _ in ranked_paths)
                rows.extend(
                    self.index._conn.execute(
                        f"""
                        SELECT f.path, c.content, c.start_line, c.end_line, c.content_hash,
                               COUNT(s.symbol_id) AS symbol_count
                        FROM chunks c
                        JOIN files f ON f.file_id = c.file_id
                        LEFT JOIN symbols s ON s.file_id = f.file_id
                             AND s.start_line BETWEEN c.start_line AND c.end_line
                        WHERE f.path IN ({placeholders})
                        GROUP BY c.chunk_id
                        ORDER BY symbol_count DESC, c.start_line
                        LIMIT ?
                        """,
                        (*ranked_paths, limit * 3),
                    ).fetchall()
                )
            rows.extend(
                self.index._conn.execute(
                    """
                    SELECT f.path, c.content, c.start_line, c.end_line, c.content_hash,
                           COUNT(s.symbol_id) AS symbol_count
                    FROM chunks c
                    JOIN files f ON f.file_id = c.file_id
                    LEFT JOIN symbols s ON s.file_id = f.file_id
                         AND s.start_line BETWEEN c.start_line AND c.end_line
                    GROUP BY c.chunk_id
                    ORDER BY symbol_count DESC, length(f.path), f.path, c.start_line
                    LIMIT ?
                    """,
                    (limit * 6,),
                ).fetchall()
            )

        by_path: dict[str, object] = {}
        path_priority = {path: idx for idx, path in enumerate(ranked_paths)}
        # Rows from PageRank paths arrive first. The second global query fills
        # gaps while `by_path` enforces cross-file diversity.
        for row in rows:
            path = row["path"]
            if path in already or path in by_path:
                continue
            by_path[path] = row
            if len(by_path) >= limit:
                break

        ordered = sorted(
            by_path.values(),
            key=lambda row: (
                path_priority.get(row["path"], 10_000),
                -int(row["symbol_count"] or 0),
                row["path"],
            ),
        )
        candidates: list[ContextCandidate] = []
        for idx, row in enumerate(ordered[:limit]):
            text = row["content"]
            candidates.append(
                ContextCandidate(
                    candidate_id=f"semantic-seed:{row['path']}:{row['start_line']}:{idx}",
                    source_type="file",
                    path=row["path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    content_hash=row["content_hash"],
                    estimated_tokens=max(1, len(text) // 4),
                    relevance=0.28,
                    confidence=0.58,
                    freshness=0.90,
                    mandatory=False,
                    trust_level="WORKSPACE_DATA",
                    dependencies=(),
                    selection_reason="Bounded structural seed for semantic fallback",
                    representation="SKELETON",
                    content=text,
                )
            )
        return candidates

    def _indexed_hash(self, path: str) -> str | None:
        try:
            with self.index._lock:
                row = self.index._conn.execute(
                    "SELECT content_hash FROM files WHERE path=?", (path,)
                ).fetchone()
            return row["content_hash"] if row else None
        except Exception:
            return None

    def _first_chunk(self, path: str):
        try:
            safe_path = self.workspace_fs.relative(path)
        except PermissionError:
            return None
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
                (safe_path,),
            ).fetchone()

    def _paired_test_paths(self, source_path: str) -> List[str]:
        path = Path(source_path)
        stem, suffix = path.stem, path.suffix
        raw_candidates = {
            str(path.with_name(f"test_{stem}{suffix}")),
            str(path.with_name(f"{stem}_test{suffix}")),
            f"tests/test_{stem}{suffix}",
            f"tests/{stem}_test{suffix}",
        }
        candidates = []
        for candidate in raw_candidates:
            try:
                candidates.append(self.workspace_fs.relative(candidate))
            except PermissionError:
                continue
        if not candidates:
            return []
        with self.index._lock:
            rows = self.index._conn.execute(
                "SELECT path FROM files WHERE path IN (%s) ORDER BY path"
                % ",".join("?" for _ in candidates),
                tuple(candidates),
            ).fetchall()
        return [row["path"] for row in rows]

    @staticmethod
    def _paths_from(*groups: list[ContextCandidate]) -> set[str]:
        return {
            candidate.path
            for group in groups
            for candidate in group
            if candidate.path is not None
        }

    @staticmethod
    def _wants_project_overview(prompt: str) -> bool:
        prompt_l = prompt.lower()
        return any(term in prompt_l for term in ("projeto", "project", "repo", "repository"))

    @staticmethod
    def _wants_git_focus(prompt: str) -> bool:
        prompt_l = prompt.lower()
        return any(
            term in prompt_l
            for term in (
                "git",
                "diff",
                "alterado",
                "alterados",
                "mudança",
                "mudanças",
                "changed",
            )
        )

    def _git_focus_paths(self, limit: int = 20) -> List[str]:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.index.root_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []
        paths = []
        for line in result.stdout.splitlines():
            if not line or len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            try:
                path = self.workspace_fs.relative(path)
            except PermissionError:
                continue
            if path != "." and not path.startswith(".kitt/"):
                paths.append(path)
            if len(paths) >= limit:
                break
        return paths
