from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from kitt.context_filter.instruction_selector import LazyInstructionSelector
from kitt.security.workspace_fs import WorkspaceFileSystem


class HierarchicalAgentsReader:
    """Discover hierarchical AGENTS plus lazy rules/specs safely and compactly."""

    def __init__(
        self,
        root_dir: str = ".",
        workspace_fs: WorkspaceFileSystem | None = None,
        max_instruction_chars: int = 16000,
    ):
        self.root_path = Path(root_dir).resolve()
        self.workspace_fs = workspace_fs or WorkspaceFileSystem(self.root_path)
        self.selector = LazyInstructionSelector(
            self.root_path,
            workspace_fs=self.workspace_fs,
            max_total_chars=max_instruction_chars,
        )

    def _read_agents(self, rel: str) -> str:
        try:
            text, _ = self.workspace_fs.read_text(rel, max_bytes=512 * 1024)
            return text
        except (FileNotFoundError, IsADirectoryError, PermissionError, ValueError, OSError):
            return ""

    def _target_query(self, target_file_path: Optional[str]) -> tuple[str, str]:
        if not target_file_path:
            return "", ""
        try:
            safe_target = self.workspace_fs.relative(target_file_path)
        except PermissionError:
            return "", ""
        query = safe_target
        try:
            data = self.workspace_fs.read_prefix(safe_target, max_bytes=16 * 1024)
            query = f"{safe_target}\n{data.content.decode('utf-8', errors='ignore')}"
        except (FileNotFoundError, IsADirectoryError, PermissionError, ValueError, OSError):
            pass
        return safe_target, query

    def get_merged_agents_rules(self, target_file_path: Optional[str] = None) -> str:
        agent_paths: List[str] = []
        if self._read_agents("AGENTS.md"):
            agent_paths.append("AGENTS.md")

        safe_target, query = self._target_query(target_file_path)
        if safe_target and safe_target != ".":
            target = Path(safe_target)
            parent = target.parent
            directories = []
            while str(parent) not in {"", "."}:
                directories.append(parent)
                parent = parent.parent
            for directory in reversed(directories):
                rel = (directory / "AGENTS.md").as_posix()
                if rel not in agent_paths and self._read_agents(rel):
                    agent_paths.append(rel)

        mandatory = []
        for rel in agent_paths:
            content = self._read_agents(rel)
            if content:
                mandatory.append(
                    self.selector.descriptor(
                        rel,
                        content,
                        kind="agents",
                        mandatory=True,
                    )
                )

        selected = self.selector.select(
            mandatory,
            query=query,
            target_path=safe_target,
        )
        sections: List[str] = []
        for item in selected:
            if item.content:
                sections.append(
                    f"--- Instructions from {item.path} [{item.kind}] ---\n"
                    f"{item.content}\n"
                )
        return "\n".join(sections)
