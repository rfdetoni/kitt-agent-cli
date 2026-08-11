from typing import List, Dict, Set
from collections import defaultdict
from kitt.domain.entities import FileTags

class ContextRanker:
    """Graph dependency builder and PageRank file ranking engine."""

    def compute_pagerank(self, nodes: List[str], edges: Dict[str, Set[str]], damping: float = 0.85, max_iter: int = 20) -> Dict[str, float]:
        n = len(nodes)
        if n == 0:
            return {}

        rank = {node: 1.0 / n for node in nodes}
        out_degree = {node: len(edges.get(node, set())) for node in nodes}

        for _ in range(max_iter):
            new_rank = {}
            for node in nodes:
                rank_sum = 0.0
                for predecessor in nodes:
                    if node in edges.get(predecessor, set()):
                        rank_sum += rank[predecessor] / max(out_degree[predecessor], 1)
                new_rank[node] = (1.0 - damping) / n + damping * rank_sum
            rank = new_rank

        return rank

    def rank_files(
        self,
        all_file_tags: List[FileTags],
        focus_files: List[str],
        focus_symbols: List[str] = None
    ) -> List[str]:
        if not all_file_tags:
            return []

        focus_symbols = focus_symbols or []
        file_paths = [ft.path for ft in all_file_tags]

        # Build symbol def & ref maps
        symbol_defined_in: Dict[str, str] = {}
        file_references: Dict[str, Set[str]] = defaultdict(set)
        file_defs: Dict[str, Set[str]] = defaultdict(set)

        for ft in all_file_tags:
            for tag in ft.tags:
                if tag.kind == 'def':
                    symbol_defined_in[tag.name] = ft.path
                    file_defs[ft.path].add(tag.name)
                elif tag.kind == 'ref':
                    file_references[ft.path].add(tag.name)

        # Build file-to-file dependency edges
        edges: Dict[str, Set[str]] = defaultdict(set)
        for src_file, refs in file_references.items():
            for ref_symbol in refs:
                target_file = symbol_defined_in.get(ref_symbol)
                if target_file and target_file != src_file:
                    edges[src_file].add(target_file)

        # Compute PageRank
        pagerank_scores = self.compute_pagerank(file_paths, edges)
        max_pr = max(pagerank_scores.values()) if pagerank_scores else 1.0

        focus_file_set = set(focus_files)
        focus_symbol_set = set(focus_symbols)

        scores: Dict[str, float] = {}
        for ft in all_file_tags:
            path = ft.path
            score = 0.0

            # 1. Explicit path boost
            if path in focus_file_set or any(ff in path for ff in focus_file_set if ff):
                score += 10.0

            # 2. Exact symbol match boost
            defs_in_file = file_defs[path]
            matched_symbols = defs_in_file.intersection(focus_symbol_set)
            score += 5.0 * len(matched_symbols)

            # 3. Normalized PageRank boost
            pr_norm = (pagerank_scores.get(path, 0.0) / max_pr) if max_pr > 0 else 0.0
            score += 2.0 * pr_norm

            # 4. Token cost penalty
            estimated_kilo_tokens = len(ft.tags) * 0.05
            score -= 0.5 * estimated_kilo_tokens

            scores[path] = score

        return sorted(file_paths, key=lambda p: scores.get(p, 0.0), reverse=True)
