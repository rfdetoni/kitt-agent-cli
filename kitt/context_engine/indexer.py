"""Compatibility adapter over the shared SQLite RepositoryIndex."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from kitt.domain.entities import FileTags, Tag
from kitt.index.repository import RepositoryIndex


class LocalFileIndexer:
    """Legacy facade kept for callers/tests; no JSON cache path remains."""

    def __init__(
        self,
        root_dir: str,
        persistence_enabled: bool = True,
        max_file_bytes: int = 512 * 1024,
        max_files: int = 20000,
        max_total_bytes: int = 256 * 1024 * 1024,
        parser_version: int = 1,
    ):
        self.root_path = Path(root_dir).resolve()
        self.persistence_enabled = persistence_enabled
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes
        self.parser_version = parser_version
        self.index = RepositoryIndex(
            self.root_path,
            in_memory=not persistence_enabled,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
        )

    def scan(self, deadline: Optional[float] = None) -> List[FileTags]:
        # ponytail: deadline kept for API compatibility; RepositoryIndex already bounds scan size/bytes.
        self.index.build_or_update()
        with self.index._lock:
            rows = self.index._conn.execute(
                """
                SELECT f.path, s.name, s.kind, s.signature, s.start_line
                FROM symbols s
                JOIN files f ON f.file_id = s.file_id
                ORDER BY f.path, s.start_line
                """
            ).fetchall()
        by_path: dict[str, list[Tag]] = {}
        for row in rows:
            by_path.setdefault(row["path"], []).append(
                Tag(
                    kind="def",
                    name=row["name"],
                    line=row["start_line"],
                    signature=row["signature"] or row["name"],
                    sub_kind=row["kind"],
                )
            )
        return [FileTags(path=path, tags=tags) for path, tags in by_path.items()]

    def close(self) -> None:
        self.index.close()
