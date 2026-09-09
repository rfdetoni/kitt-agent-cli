"""O(V+E) PageRank and sub-graph expansion for repository index graph."""

from __future__ import annotations

from typing import Dict, Set, List, Tuple


class RepositoryGraph:
    """Graph structure with O(1) edge updates and O(V+E) PageRank iterations."""

    def __init__(self):
        self.adj: Dict[str, List[Tuple[str, float]]] = {}
        self.rev_adj: Dict[str, List[Tuple[str, float]]] = {}
        self.nodes: Set[str] = set()
        self.cached_scores: Dict[str, float] = {}
        self.generation: int = 0
        self._edge_positions: Dict[Tuple[str, str], Tuple[int, int]] = {}

    def add_edge(self, src: str, dst: str, weight: float = 1.0, kind: str = "import") -> None:
        self.nodes.add(src)
        self.nodes.add(dst)
        edge_key = (src, dst)
        positions = self._edge_positions.get(edge_key)

        if positions is None:
            src_edges = self.adj.setdefault(src, [])
            dst_edges = self.rev_adj.setdefault(dst, [])
            self._edge_positions[edge_key] = (len(src_edges), len(dst_edges))
            src_edges.append((dst, weight))
            dst_edges.append((src, weight))
            self.generation += 1
            return

        src_idx, dst_idx = positions
        if weight > self.adj[src][src_idx][1]:
            self.adj[src][src_idx] = (dst, weight)
            self.rev_adj[dst][dst_idx] = (src, weight)
            self.generation += 1

    def compute_pagerank(
        self,
        damping: float = 0.85,
        max_iterations: int = 20,
        tol: float = 1e-4
    ) -> Dict[str, float]:
        """Compute PageRank in O(V+E) time per iteration."""
        if not self.nodes:
            return {}

        num_nodes = len(self.nodes)
        node_list = list(self.nodes)
        initial_val = 1.0 / num_nodes
        scores = {n: initial_val for n in node_list}

        out_sums = {}
        for src in node_list:
            out_sums[src] = sum(w for _, w in self.adj.get(src, []))

        base_score = (1.0 - damping) / num_nodes

        for _ in range(max_iterations):
            new_scores = {}
            max_diff = 0.0
            dangling_sum = sum(scores[src] for src in node_list if out_sums.get(src, 0.0) <= 0) / num_nodes

            for dst in node_list:
                incoming_sum = dangling_sum
                for src, weight in self.rev_adj.get(dst, []):
                    total_out = out_sums.get(src, 0.0)
                    if total_out > 0:
                        incoming_sum += (scores[src] * weight) / total_out

                new_val = base_score + damping * incoming_sum
                max_diff = max(max_diff, abs(new_val - scores[dst]))
                new_scores[dst] = new_val

            scores = new_scores
            if max_diff < tol:
                break

        self.cached_scores = scores
        return scores

    def expand_neighborhood(self, seed_nodes: Set[str], max_hops: int = 2, max_nodes: int = 50) -> Set[str]:
        """Expand neighborhood via BFS up to max_hops and max_nodes limit."""
        visited = set(seed_nodes)
        current_layer = set(seed_nodes)

        for _ in range(max_hops):
            if len(visited) >= max_nodes:
                break
            next_layer = set()
            for node in current_layer:
                neighbors = [*self.adj.get(node, []), *self.rev_adj.get(node, [])]
                for neighbor, _ in neighbors:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_layer.add(neighbor)
                        if len(visited) >= max_nodes:
                            break
                if len(visited) >= max_nodes:
                    break
            current_layer = next_layer

        return visited
