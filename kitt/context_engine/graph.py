from typing import List, Dict, Set
from collections import defaultdict
from kitt.domain.entities import FileTags

class ContextRanker:
    """Graph reference building and PageRank file ranking."""

    def rank_files(self, all_file_tags: List[FileTags], focus_files: List[str]) -> List[str]:
        if not all_file_tags:
            return []

        # Map defined symbols to files
        symbol_to_files: Dict[str, Set[str]] = defaultdict(set)
        for ft in all_file_tags:
            for tag in ft.tags:
                if tag.kind == 'def':
                    symbol_to_files[tag.name].add(ft.path)

        # Build in-degree counts per file
        in_degree: Dict[str, float] = defaultdict(float)
        focus_set = set(focus_files)

        for ft in all_file_tags:
            score = len(ft.tags)
            if ft.path in focus_set:
                score += 50.0  # Focus boost
            in_degree[ft.path] = score

        # Sort files by score descending
        sorted_files = sorted(
            [ft.path for ft in all_file_tags],
            key=lambda path: in_degree[path],
            reverse=True
        )

        return sorted_files
