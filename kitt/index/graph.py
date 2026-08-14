"""O(V+E) PageRank and sub-graph expansion for repository index graph."""

from __future__ import annotations

from typing import Dict, Set, List, Tuple


class RepositoryGraph:
    """Graph structure with O(V+E) PageRank and bounded neighborhood expansion."""

    def __init__(self):
        self.adj: Dict[str, List[Tuple[str, float]]] = {}
        self.rev_adj: Dict[str, List[Tuple[str, float]]] = {}
        self.nodes: Set[str] = set()
        self.cached_scores: Dict[str, float] = {}
        self.generation: int = 0

    def add_edge(self, src: str, dst: str, weight: float = 1.0, kind: str = "import") -> None:
        self.nodes.add(src)
        self.nodes.add(dst)

        if src not in self.adj:
            self.adj[src] = []
        if not any(existing == dst for existing, _ in self.adj[src]):
            self.adj[src].append((dst, weight))

        if dst not in self.rev_adj:
            self.rev_adj[dst] = []
        if not any(existing == src for existing, _ in self.rev_adj[dst]):
            self.rev_adj[dst].append((src, weight))

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

        # Precompute out-weights for O(V+E) iteration
        out_sums = {}
        for src in node_list:
            out_sums[src] = sum(w for _, w in self.adj.get(src, []))

        base_score = (1.0 - damping) / num_nodes

        for _ in range(max_iterations):
            new_scores = {}
            max_diff = 0.0

            for dst in node_list:
                incoming_sum = 0.0
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
                for neighbor, _ in self.adj.get(node, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_layer.add(neighbor)
                        if len(visited) >= max_nodes:
                            break
                if len(visited) >= max_nodes:
                    break
            current_layer = next_layer

        return visited
