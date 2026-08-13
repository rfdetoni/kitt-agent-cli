import os
import re
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Set, Optional, Any
from kitt.domain.entities import ContextBlock, TaskFocus, FileTags, Tag
from kitt.context_engine.parser import SymbolParser
from kitt.context_engine.graph import ContextRanker

IGNORED_DIRS = {
    '.git', '.kitt', 'node_modules', '__pycache__', 'dist', 'build',
    'coverage', '.venv', 'venv', '.idea', '.vscode'
}

class ContextEngine:
    """Incremental Context Engine facade with mtime/hash caching and PageRank symbol graph integration."""

    def __init__(self, persistence_enabled: bool = True):
        self.parser = SymbolParser()
        self.ranker = ContextRanker()
        self.persistence_enabled = persistence_enabled

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
        focus = self.extract_task_focus(task_description)
        
        from kitt.context_engine.indexer import LocalFileIndexer
        indexer = LocalFileIndexer(str(root_path))
        all_file_tags = indexer.scan()

        file_tags_map = {ft.path: ft for ft in all_file_tags}
        ranked_file_paths = self.ranker.rank_files(all_file_tags, focus.focus_files, focus.focus_symbols)

        blocks: List[ContextBlock] = []
        current_tokens = 0

        for file_path in ranked_file_paths:
            ft = file_tags_map.get(file_path)
            if not ft:
                continue

            defs = [t for t in ft.tags if t.kind == 'def']
            if not defs:
                continue

            lines = [f"{file_path}:"]
            last_line = 0
            for tag in sorted(defs, key=lambda x: x.line):
                if tag.line > last_line + 1:
                    lines.append("⋮")
                lines.append(f"  {tag.signature}")
                last_line = tag.line
            lines.append("⋮")

            content = "\n".join(lines)
            tokens = len(content) // 4

            if current_tokens + tokens > max_tokens:
                continue

            blocks.append(ContextBlock(path=file_path, content=content, token_count=tokens))
            current_tokens += tokens

        return blocks
