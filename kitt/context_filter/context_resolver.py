from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Tuple

from kitt.context_engine.agents_reader import HierarchicalAgentsReader
from kitt.context_filter.prompt_budget import TokenCounter
from kitt.security.workspace_fs import WorkspaceFileSystem

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
    """Resolve explicit context exclusively through the workspace trust boundary."""

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()
        self.workspace_fs = WorkspaceFileSystem(self.root_path)
        self.agents_reader = HierarchicalAgentsReader(
            root_dir=root_dir,
            workspace_fs=self.workspace_fs,
        )

    def is_safe_path(self, rel_path: str) -> Tuple[bool, Optional[Path]]:
        try:
            rel = self.workspace_fs.relative(rel_path)
            if rel == ".":
                return False, None
            self.workspace_fs.stat_regular(rel)
            return True, self.workspace_fs.absolute_lexical(rel)
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
            return False, None

    def resolve_agents_instructions(
        self,
        target_file_path: Optional[str] = None,
    ) -> List[ContextItem]:
        merged = self.agents_reader.get_merged_agents_rules(target_file_path)
        if not merged or self._agents_incoherent_with_workspace(merged):
            return []
        return [
            ContextItem(
                source="AGENTS.md",
                content=merged,
                priority=9.0,
                mandatory=True,
                estimated_tokens=TokenCounter.count_tokens(merged),
            )
        ]

    def _agents_incoherent_with_workspace(self, content: str) -> bool:
        lower = content.lower()
        python_markers = (self.root_path / "kitt").is_dir() or (
            self.root_path / "pyproject.toml"
        ).exists()
        node_markers = (self.root_path / "package.json").exists() or (
            self.root_path / "src"
        ).is_dir()
        claims_node_stack = all(term in lower for term in ("node", "bun", "typescript"))
        return python_markers and claims_node_stack and not node_markers

    def resolve_explicit_files(
        self,
        file_paths: List[str],
        max_lines_per_file: int = 240,
    ) -> List[ContextItem]:
        items: List[ContextItem] = []
        # 2 MiB is ample for a 240-line excerpt and prevents accidental giant
        # explicit-file loads before line truncation is applied.
        read_limit = min(self.workspace_fs.max_file_bytes, 2 * 1024 * 1024)
        for path_str in file_paths:
            try:
                rel_str = self.workspace_fs.relative(path_str)
                data = self.workspace_fs.read_prefix(rel_str, max_bytes=read_limit)
            except (FileNotFoundError, IsADirectoryError, PermissionError, ValueError, OSError):
                continue

            lines = data.content.decode("utf-8", errors="ignore").splitlines()
            omitted = 0
            if len(lines) > max_lines_per_file:
                omitted = len(lines) - max_lines_per_file
                lines = lines[:max_lines_per_file]
            elif not data.complete:
                omitted = 1
            body = "\n".join(lines)
            if omitted:
                body += f"\n... [{omitted if omitted > 1 else 'additional'} lines/content omitted]"

            # This is presentation text only. The actual file body is still
            # untrusted data and is subordinated by the system/tool contract.
            formatted = (
                f"--- TARGET FILE TO EDIT: {rel_str} (Use exact path: '{rel_str}') ---\n"
                f"{body}\n--- end {rel_str} ---"
            )
            items.append(
                ContextItem(
                    source=rel_str,
                    content=formatted,
                    priority=10.0,
                    mandatory=False,
                    estimated_tokens=TokenCounter.count_tokens(formatted),
                )
            )
        return items
