from pathlib import Path
from typing import List, Optional, Set, Tuple
from dataclasses import dataclass, field
from kitt.domain.entities import SemanticTask, ContextPlan
from kitt.context_filter.prompt_budget import TokenCounter
from kitt.context_engine.agents_reader import HierarchicalAgentsReader

FORBIDDEN_PATTERNS: Set[str] = {".git", ".env"}

@dataclass
class ContextItem:
    source: str
    content: str
    priority: float
    mandatory: bool = False
    estimated_tokens: int = 0

@dataclass
class ResolvedContext:
    task: str
    mandatory_constraints: List[str] = field(default_factory=list)
    instructions: List[ContextItem] = field(default_factory=list)
    repo_map: List[ContextItem] = field(default_factory=list)
    source_snippets: List[ContextItem] = field(default_factory=list)
    history: List[ContextItem] = field(default_factory=list)
    recent_results: List[ContextItem] = field(default_factory=list)

class ContextResolver:
    """Resolves ContextPlan and SemanticTask into prioritized, structured ContextItems."""

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()
        self.agents_reader = HierarchicalAgentsReader(root_dir=root_dir)

    def is_safe_path(self, rel_path: str) -> Tuple[bool, Optional[Path]]:
        try:
            raw_p = Path(rel_path)
            full_p = (self.root_path / raw_p).resolve() if not raw_p.is_absolute() else raw_p.resolve()

            if not full_p.is_relative_to(self.root_path):
                return False, None

            rel = full_p.relative_to(self.root_path)
            for part in rel.parts:
                if part in FORBIDDEN_PATTERNS or part.startswith(".env"):
                    return False, None

            return True, full_p
        except Exception:
            return False, None

    def resolve_agents_instructions(self, target_file_path: Optional[str] = None) -> List[ContextItem]:
        merged = self.agents_reader.get_merged_agents_rules(target_file_path)
        if not merged:
            return []
        if self._agents_incoherent_with_workspace(merged):
            return []
        tokens = TokenCounter.count_tokens(merged)
        return [
            ContextItem(
                source="AGENTS.md",
                content=merged,
                priority=9.0,
                mandatory=True,
                estimated_tokens=tokens
            )
        ]

    def _agents_incoherent_with_workspace(self, content: str) -> bool:
        lower = content.lower()
        python_markers = (self.root_path / "kitt").is_dir() or (self.root_path / "pyproject.toml").exists()
        node_markers = (self.root_path / "package.json").exists() or (self.root_path / "src").is_dir()
        claims_node_stack = all(term in lower for term in ("node", "bun", "typescript"))
        return python_markers and claims_node_stack and not node_markers

    def resolve_explicit_files(self, file_paths: List[str], max_lines_per_file: int = 240) -> List[ContextItem]:
        items: List[ContextItem] = []
        for path_str in file_paths:
            is_safe, full_p = self.is_safe_path(path_str)
            if not is_safe or not full_p or not full_p.exists() or not full_p.is_file():
                continue

            try:
                rel_str = str(full_p.relative_to(self.root_path))
                lines = full_p.read_text(encoding='utf-8', errors='ignore').splitlines()

                if len(lines) > max_lines_per_file:
                    content = "\n".join(lines[:max_lines_per_file]) + f"\n... [{len(lines) - max_lines_per_file} lines omitted]"
                else:
                    content = "\n".join(lines)

                formatted = f"--- {rel_str} ---\n{content}\n--- end {rel_str} ---"
                tokens = TokenCounter.count_tokens(formatted)

                items.append(
                    ContextItem(
                        source=rel_str,
                        content=formatted,
                        priority=10.0,
                        mandatory=False,
                        estimated_tokens=tokens
                    )
                )
            except Exception:
                continue

        return items
