import time
from pathlib import Path
from typing import List
from kitt.domain.entities import ContextBlock, TaskFocus
from kitt.context.compiler import ContextCompiler, CompiledContext
from kitt.context.query_plan import QueryPlanner
from kitt.context.retrieval import HybridRetrievalPipeline
from kitt.context.cache import ContextCache
from kitt.context_engine.parser import SymbolParser
from kitt.index.repository import RepositoryIndex

IGNORED_DIRS = {
    '.git', '.kitt', 'node_modules', '__pycache__', 'dist', 'build',
    'coverage', '.venv', 'venv', '.idea', '.vscode'
}

class ContextEngine:
    """Incremental Context Engine facade with mtime/hash caching and PageRank symbol graph integration."""

    def __init__(self, repository_index=None, persistence_enabled: bool = True, cache: ContextCache | None = None):
        self.parser = SymbolParser()
        self.index = repository_index
        self.persistence_enabled = persistence_enabled
        self.compiler = ContextCompiler()
        self.cache = cache or ContextCache()
        self.last_compiled_context: CompiledContext | None = None
        self.last_build_stats = {}

    def extract_task_focus(self, task_description: str) -> TaskFocus:
        if not task_description:
            return TaskFocus()
        plan = QueryPlanner.plan(task_description)
        return TaskFocus(focus_files=list(plan.exact_paths), focus_symbols=list(plan.exact_symbols))

    def get_relevant_context(
        self,
        task_description: str,
        max_tokens: int = 2048,
        root_dir: str = ".",
        working_set_paths: List[str] | None = None,
    ) -> List[ContextBlock]:
        root_path = Path(root_dir).resolve()
        if self.index is None or self.index.root_path != root_path:
            self.index = RepositoryIndex(root_path, in_memory=not self.persistence_enabled)

        started = time.time()
        if not working_set_paths:
            cached = self.cache.get(task_description, self.index.index_generation(), max_tokens)
            if cached:
                self.last_compiled_context = cached
                if not cached.text:
                    return []
                return [ContextBlock(path="ContextPack", content=cached.text, token_count=cached.total_tokens)]

        plan = QueryPlanner.plan(task_description, token_budget=max_tokens)
        bootstrap_paths = list(dict.fromkeys([*plan.exact_paths, *list(working_set_paths or [])]))
        if self.index.index_generation() == 0 and bootstrap_paths:
            stats = self.index.bootstrap_then_background(bootstrap_paths)
        elif bootstrap_paths:
            # Targeted freshness check; do not rescan unrelated repository files.
            stats = self.index.update_paths(bootstrap_paths)
        else:
            stats = self.index.ready_stats() if self.index.index_generation() else self.index.build_or_update()
        selected, rejected, plan = HybridRetrievalPipeline(self.index).retrieve_with_rejections(
            task_description,
            explicit_files=set(plan.exact_paths),
            max_tokens=max_tokens,
            plan=plan,
            working_set_paths=set(working_set_paths or ()),
        )
        compiled = self.compiler.compile(
            plan,
            selected,
            rejected,
            generation=stats.get("generation", 0),
            partial=stats.get("state") == "PARTIAL",
        )
        self.last_compiled_context = compiled
        self.cache.put(task_description, stats.get("generation", 0), compiled, max_tokens)
        self.last_build_stats = {
            **stats,
            "duration_ms": int((time.time() - started) * 1000),
            "selected": compiled.selected_count,
            "rejected": compiled.rejected_count,
            "tokens": compiled.total_tokens,
            "coverage": compiled.quality.coverage,
            "degraded": compiled.quality.degraded,
        }
        if not compiled.text:
            return []
        return [ContextBlock(path="ContextPack", content=compiled.text, token_count=compiled.total_tokens)]
