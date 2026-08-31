from __future__ import annotations

import time
from pathlib import Path
from typing import List

from kitt.context.cache import ContextCache
from kitt.context.compiler import CompiledContext, ContextCompiler
from kitt.context.query_plan import QueryPlanner
from kitt.context.retrieval import HybridRetrievalPipeline
from kitt.context_engine.parser import SymbolParser
from kitt.domain.entities import ContextBlock, TaskFocus
from kitt.index.repository import RepositoryIndex

IGNORED_DIRS = {
    ".git", ".kitt", "node_modules", "__pycache__", "dist", "build",
    "coverage", ".venv", "venv", ".idea", ".vscode",
}


class ContextEngine:
    """Incremental Context Engine facade with freshness-aware hybrid Code-RAG."""

    def __init__(
        self,
        repository_index=None,
        persistence_enabled: bool = True,
        cache: ContextCache | None = None,
        refresh_interval_seconds: float = 5.0,
    ):
        self.parser = SymbolParser()
        self.index = repository_index
        self._owns_index = repository_index is None
        self.persistence_enabled = persistence_enabled
        self.compiler = ContextCompiler()
        self.cache = cache or ContextCache()
        self.refresh_interval_seconds = max(0.0, float(refresh_interval_seconds))
        self._last_refresh_monotonic = 0.0
        self._retrieval: HybridRetrievalPipeline | None = None
        self._retrieval_index_identity: int | None = None
        self.last_compiled_context: CompiledContext | None = None
        self.last_build_stats: dict[str, object] = {}

    def close(self) -> None:
        if self._owns_index and self.index is not None:
            try:
                self.index.close()
            finally:
                self.index = None
        self._retrieval = None
        self._retrieval_index_identity = None

    def extract_task_focus(self, task_description: str) -> TaskFocus:
        if not task_description:
            return TaskFocus()
        plan = QueryPlanner.plan(task_description)
        return TaskFocus(
            focus_files=list(plan.exact_paths),
            focus_symbols=list(plan.exact_symbols),
        )

    def _ensure_index(self, root_path: Path) -> RepositoryIndex:
        if self.index is not None and self.index.root_path == root_path:
            return self.index
        if self._owns_index and self.index is not None:
            self.index.close()
        self.index = RepositoryIndex(root_path, in_memory=not self.persistence_enabled)
        self._owns_index = True
        self._retrieval = None
        self._retrieval_index_identity = None
        self._last_refresh_monotonic = 0.0
        self.cache.clear()
        return self.index

    def _get_retrieval(self) -> HybridRetrievalPipeline:
        assert self.index is not None
        identity = id(self.index)
        if self._retrieval is None or self._retrieval_index_identity != identity:
            self._retrieval = HybridRetrievalPipeline(self.index)
            self._retrieval_index_identity = identity
        return self._retrieval

    def _refresh_index(
        self,
        bootstrap_paths: list[str],
    ) -> dict[str, object]:
        assert self.index is not None
        generation = self.index.index_generation()
        now = time.monotonic()
        if generation == 0:
            if bootstrap_paths:
                stats = self.index.bootstrap_then_background(bootstrap_paths)
            else:
                stats = self.index.build_or_update()
            self._last_refresh_monotonic = now
            return stats

        if bootstrap_paths:
            stats = self.index.update_paths(bootstrap_paths)
            self._last_refresh_monotonic = now
            return stats

        if (
            self.refresh_interval_seconds == 0
            or now - self._last_refresh_monotonic >= self.refresh_interval_seconds
        ):
            stats = self.index.build_or_update()
            self._last_refresh_monotonic = now
            return stats

        return self.index.ready_stats()

    def _cache_namespace(self, root_path: Path, retrieval: HybridRetrievalPipeline) -> str:
        config = getattr(retrieval, "rag_config", None)
        fingerprint = config.fingerprint() if config is not None and hasattr(config, "fingerprint") else "default"
        return f"{root_path}:{fingerprint}"

    def get_relevant_context(
        self,
        task_description: str,
        max_tokens: int = 2048,
        root_dir: str = ".",
        working_set_paths: List[str] | None = None,
    ) -> List[ContextBlock]:
        started = time.monotonic()
        root_path = Path(root_dir).resolve()
        index = self._ensure_index(root_path)
        plan = QueryPlanner.plan(task_description, token_budget=max_tokens)
        working_set = list(working_set_paths or [])
        bootstrap_paths = list(dict.fromkeys([*plan.exact_paths, *working_set]))

        # Freshness is established before cache lookup. Targeted paths are
        # refreshed immediately; unrelated repositories use a bounded refresh
        # interval to avoid a full scan on every prompt.
        stats = self._refresh_index(bootstrap_paths)
        retrieval = self._get_retrieval()
        namespace = self._cache_namespace(root_path, retrieval)
        generation = int(stats.get("generation", index.index_generation()))

        if not working_set:
            cached = self.cache.get(
                task_description,
                generation,
                max_tokens,
                namespace=namespace,
            )
            if cached is not None:
                self.last_compiled_context = cached
                self.last_build_stats = {
                    **stats,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "selected": cached.selected_count,
                    "rejected": cached.rejected_count,
                    "tokens": cached.total_tokens,
                    "coverage": cached.quality.coverage,
                    "degraded": cached.quality.degraded,
                    "cache_hit": True,
                    "retrieval": {},
                }
                if not cached.text:
                    return []
                return [
                    ContextBlock(
                        path="ContextPack",
                        content=cached.text,
                        token_count=cached.total_tokens,
                    )
                ]

        selected, rejected, plan = retrieval.retrieve_with_rejections(
            task_description,
            explicit_files=set(plan.exact_paths),
            max_tokens=max_tokens,
            plan=plan,
            working_set_paths=set(working_set),
        )
        compiled = self.compiler.compile(
            plan,
            selected,
            rejected,
            generation=generation,
            partial=stats.get("state") == "PARTIAL",
        )
        self.last_compiled_context = compiled
        # Context built with a conversation working-set must never seed the
        # generic prompt cache because the key intentionally omits that set.
        if not working_set:
            self.cache.put(
                task_description,
                generation,
                compiled,
                max_tokens,
                namespace=namespace,
            )
        self.last_build_stats = {
            **stats,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "selected": compiled.selected_count,
            "rejected": compiled.rejected_count,
            "tokens": compiled.total_tokens,
            "coverage": compiled.quality.coverage,
            "degraded": compiled.quality.degraded,
            "cache_hit": False,
            "retrieval": retrieval.last_retrieval_stats,
        }
        if not compiled.text:
            return []
        return [
            ContextBlock(
                path="ContextPack",
                content=compiled.text,
                token_count=compiled.total_tokens,
            )
        ]
