import os
import re
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Set, Optional
from kitt.domain.entities import ContextBlock, TaskFocus, FileTags, Tag
from kitt.context_engine.parser import SymbolParser
from kitt.context_engine.graph import ContextRanker

IGNORED_DIRS = {
    '.git', '.kitt', 'node_modules', '__pycache__', 'dist', 'build',
    'coverage', '.venv', 'venv', '.idea', '.vscode'
}

class ContextEngine:
    """Incremental Context Engine facade with mtime/hash caching and PageRank symbol graph integration."""

    def __init__(self):
        self.parser = SymbolParser()
        self.ranker = ContextRanker()

    def _compute_file_meta(self, path: Path) -> Dict[str, Any]:
        stat = path.stat()
        mtime = stat.st_mtime
        size = stat.st_size
        return {"mtime": mtime, "size": size}

    def _get_cache_dir(self, root_path: Path) -> Path:
        cache_dir = root_path / ".kitt" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "index_cache.json"

    def _load_cache(self, cache_file: Path) -> Dict[str, dict]:
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding='utf-8'))
            except Exception:
                return {}
        return {}

    def _save_cache(self, cache_file: Path, data: Dict[str, dict]):
        try:
            cache_file.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except Exception:
            pass

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
        cache_file = self._get_cache_dir(root_path)
        cache = self._load_cache(cache_file)
        new_cache = {}

        all_file_tags: List[FileTags] = []
        cache_hits = 0
        cache_misses = 0

        for path in root_path.rglob('*'):
            if path.is_file() and not any(part in IGNORED_DIRS for part in path.parts):
                try:
                    rel_path = str(path.relative_to(root_path))
                    stat = path.stat()
                    mtime = stat.st_mtime
                    size = stat.st_size

                    cached_entry = cache.get(rel_path)
                    if cached_entry and cached_entry.get("mtime") == mtime and cached_entry.get("size") == size:
                        # Re-use cached tags
                        tags = [Tag(**t) for t in cached_entry.get("tags", [])]
                        ft = FileTags(path=rel_path, tags=tags)
                        all_file_tags.append(ft)
                        new_cache[rel_path] = cached_entry
                        cache_hits += 1
                    else:
                        # Re-index changed file
                        ft = self.parser.extract_file_tags(path, rel_path)
                        if ft and ft.tags:
                            all_file_tags.append(ft)
                            tags_data = [t.__dict__ for t in ft.tags]
                            new_cache[rel_path] = {"mtime": mtime, "size": size, "tags": tags_data}
                        cache_misses += 1
                except Exception:
                    continue

        self._save_cache(cache_file, new_cache)

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
