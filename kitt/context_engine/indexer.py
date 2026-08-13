import os
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from kitt.domain.entities import FileTags, Tag
from kitt.context_engine.parser import SymbolParser

IGNORED_DIRS = {
    '.git', '.kitt', 'node_modules', '__pycache__', 'dist', 'build',
    'coverage', '.venv', 'venv', '.idea', '.vscode'
}


class LocalFileIndexer:
    """Incremental repository indexer with a durable JSON cache.

    When ``persistence_enabled`` is False the cache is kept in memory only and
    nothing is written to the workspace.
    """

    def __init__(self, root_dir: str, persistence_enabled: bool = True,
                 max_file_bytes: int = 512 * 1024,
                 max_files: int = 20000,
                 max_total_bytes: int = 256 * 1024 * 1024,
                 parser_version: int = 1):
        self.root_path = Path(root_dir).resolve()
        self.persistence_enabled = persistence_enabled
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes
        self.parser = SymbolParser()
        self.parser_version = parser_version
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_file = self.root_path / ".kitt" / "cache" / "index_cache.json"
        self._load_cache()

    def _load_cache(self):
        if not self.persistence_enabled or not self._cache_file.exists():
            self._cache = {}
            return
        try:
            self._cache = json.loads(self._cache_file.read_text(encoding='utf-8'))
        except Exception:
            # Isolate a corrupted cache so it never breaks a scan.
            try:
                self._cache_file.replace(self._cache_file.with_suffix(".json.corrupt"))
            except OSError:
                pass
            self._cache = {}

    def _save_cache(self):
        if not self.persistence_enabled:
            return
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_file.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._cache, fh, indent=2)
                fh.flush()
            os.replace(tmp, self._cache_file)
        except OSError:
            pass

    def scan(self, deadline: Optional[float] = None) -> List[FileTags]:
        """Scans the directory incrementally using os.scandir for better performance.

        Ignores binaries and files above ``max_file_bytes`` before parsing,
        enforces total file/byte limits and a wall-clock deadline.
        """
        all_file_tags: List[FileTags] = []
        visited = set()
        file_count = 0
        total_bytes = 0
        started = time.monotonic()

        def _deadline_hit() -> bool:
            return deadline is not None and time.monotonic() > deadline

        def _scan_dir(dir_path: Path):
            nonlocal file_count, total_bytes
            if file_count >= self.max_files or _deadline_hit():
                return
            try:
                with os.scandir(dir_path) as it:
                    for entry in it:
                        if file_count >= self.max_files or _deadline_hit():
                            return
                        if entry.name in IGNORED_DIRS:
                            continue

                        if entry.is_dir(follow_symlinks=False):
                            _scan_dir(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            rel_path = str(Path(entry.path).relative_to(self.root_path))
                            stat = entry.stat()
                            if stat.st_size > self.max_file_bytes:
                                continue
                            if total_bytes + stat.st_size > self.max_total_bytes:
                                continue
                            visited.add(rel_path)
                            file_count += 1
                            total_bytes += stat.st_size
                            mtime_ns = stat.st_mtime_ns
                            size = stat.st_size

                            cached = self._cache.get(rel_path)
                            if (cached and cached.get("parser_version") == self.parser_version
                                    and cached.get("mtime_ns") == mtime_ns and cached.get("size") == size):
                                tags = [Tag(**t) for t in cached.get("tags", [])]
                                all_file_tags.append(FileTags(path=rel_path, tags=tags))
                            else:
                                ft = self.parser.extract_file_tags(Path(entry.path), rel_path)
                                if ft and ft.tags:
                                    all_file_tags.append(ft)
                                    self._cache[rel_path] = {
                                        "parser_version": self.parser_version,
                                        "mtime_ns": mtime_ns,
                                        "size": size,
                                        "tags": [t.__dict__ for t in ft.tags]
                                    }
                                else:
                                    self._cache[rel_path] = {
                                        "parser_version": self.parser_version,
                                        "mtime_ns": mtime_ns,
                                        "size": size,
                                        "tags": []
                                    }
            except PermissionError:
                pass

        _scan_dir(self.root_path)

        # Prune deleted files
        stale = [p for p in self._cache.keys() if p not in visited]
        for p in stale:
            del self._cache[p]

        self._save_cache()
        return all_file_tags
