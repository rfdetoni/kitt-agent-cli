from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from kitt.context_filter import instruction_selector_core as _core
from kitt.context_filter.instruction_selector_core import InstructionDescriptor
from kitt.context_filter.prompt_budget import TokenCounter


@dataclass(frozen=True)
class InstructionSelectionStats:
    considered_files: int = 0
    considered_chars: int = 0
    considered_tokens: int = 0
    injected_files: int = 0
    injected_chars: int = 0
    injected_tokens: int = 0
    dependency_edges: int = 0
    cycle_cuts: int = 0
    budget_chars: int = 0

    @property
    def saved_chars(self) -> int:
        return max(0, self.considered_chars - self.injected_chars)

    @property
    def saved_tokens(self) -> int:
        return max(0, self.considered_tokens - self.injected_tokens)

    def as_dict(self) -> dict[str, int]:
        return {
            "considered_files": self.considered_files,
            "considered_chars": self.considered_chars,
            "considered_tokens": self.considered_tokens,
            "injected_files": self.injected_files,
            "injected_chars": self.injected_chars,
            "injected_tokens": self.injected_tokens,
            "saved_chars": self.saved_chars,
            "saved_tokens": self.saved_tokens,
            "dependency_edges": self.dependency_edges,
            "cycle_cuts": self.cycle_cuts,
            "budget_chars": self.budget_chars,
        }


class LazyInstructionSelector(_core.LazyInstructionSelector):
    """Lazy selector with deterministic gain and dependency-graph telemetry."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_stats = InstructionSelectionStats(budget_chars=self.max_total_chars)
        self._last_discovered: list[InstructionDescriptor] = []

    def discover_optional(self) -> list[InstructionDescriptor]:
        discovered = super().discover_optional()
        self._last_discovered = discovered
        return discovered

    @staticmethod
    def _graph_stats(items: list[InstructionDescriptor]) -> tuple[int, int]:
        by_name = {item.name: item for item in items}
        edges = 0
        cycles = 0
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            nonlocal edges, cycles
            if name in visited:
                return
            if name in visiting:
                cycles += 1
                return
            item = by_name.get(name)
            if item is None:
                return
            visiting.add(name)
            for dependency in item.depends_on:
                if dependency in by_name:
                    edges += 1
                    visit(dependency)
            visiting.remove(name)
            visited.add(name)

        for name in sorted(by_name):
            visit(name)
        return edges, cycles

    def select(
        self,
        mandatory: Iterable[InstructionDescriptor],
        *,
        query: str = "",
        target_path: str = "",
        max_optional: int = 8,
    ) -> list[InstructionDescriptor]:
        mandatory_items = list(mandatory)
        result = super().select(
            mandatory_items,
            query=query,
            target_path=target_path,
            max_optional=max_optional,
        )
        candidates = [*mandatory_items, *self._last_discovered]
        considered_chars = sum(len(item.content) for item in candidates)
        injected_chars = sum(len(item.content) for item in result)
        edges, cycles = self._graph_stats(self._last_discovered)
        self.last_stats = InstructionSelectionStats(
            considered_files=len(candidates),
            considered_chars=considered_chars,
            considered_tokens=sum(TokenCounter.count_tokens(item.content) for item in candidates),
            injected_files=len(result),
            injected_chars=injected_chars,
            injected_tokens=sum(TokenCounter.count_tokens(item.content) for item in result),
            dependency_edges=edges,
            cycle_cuts=cycles,
            budget_chars=self.max_total_chars,
        )
        return result


__all__ = ["InstructionDescriptor", "InstructionSelectionStats", "LazyInstructionSelector"]
