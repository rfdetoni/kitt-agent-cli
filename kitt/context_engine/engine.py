import re
import time
from pathlib import Path
from typing import List
from kitt.domain.entities import ContextBlock, TaskFocus
from kitt.context.compiler import ContextCompiler, CompiledContext
from kitt.context.query_plan import QueryPlanner
from kitt.context.retrieval import HybridRetrievalPipeline
from kitt.context_engine.parser import SymbolParser
from kitt.index.repository import RepositoryIndex

IGNORED_DIRS = {
    '.git', '.kitt', 'node_modules', '__pycache__', 'dist', 'build',
    'coverage', '.venv', 'venv', '.idea', '.vscode'
}

class ContextEngine:
    """Incremental Context Engine facade with mtime/hash caching and PageRank symbol graph integration."""

    def __init__(self, repository_index=None, persistence_enabled: bool = True):
        self.parser = SymbolParser()
        self.index = repository_index
        self.persistence_enabled = persistence_enabled
        self.compiler = ContextCompiler()
        self.last_compiled_context: CompiledContext | None = None
        self.last_build_stats = {}

    def extract_task_focus(self, task_description: str) -> TaskFocus:
        if not task_description:
            return TaskFocus()

        file_regex = re.compile(r'[a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+')
        matched_files = list(set(file_regex.findall(task_description)))

        symbol_regex = re.compile(r'\b[A-Za-z_$][A-Za-z0-9_$]{2,}\b')
        matched_symbols = list(set(symbol_regex.findall(task_description)))

        stopwords = {
            'the', 'and', 'for', 'that', 'with', 'this', 'from', 'have', 'file',
            'function', 'class', 'method', 'add', 'create', 'update', 'fix', 'remove',
            'delete', 'change', 'make', 'use', 'code', 'task', 'test', 'repo'
        }
        focus_symbols = [s for s in matched_symbols if s.lower() not in stopwords]

        return TaskFocus(focus_files=matched_files, focus_symbols=focus_symbols)

    def get_relevant_context(
        self,
        task_description: str,
        max_tokens: int = 2048,
        root_dir: str = "."
    ) -> List[ContextBlock]:
        root_path = Path(root_dir).resolve()
        if self.index is None or self.index.root_path != root_path:
            self.index = RepositoryIndex(root_path, in_memory=not self.persistence_enabled)

        started = time.time()
        stats = self.index.build_or_update()
        plan = QueryPlanner.plan(task_description, token_budget=max_tokens)
        selected, rejected, plan = HybridRetrievalPipeline(self.index).retrieve_with_rejections(
            task_description,
            explicit_files=set(plan.exact_paths),
            max_tokens=max_tokens,
            plan=plan,
        )
        compiled = self.compiler.compile(
            plan,
            selected,
            rejected,
            generation=stats.get("generation", 0),
            partial=stats.get("state") == "PARTIAL",
        )
        self.last_compiled_context = compiled
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
