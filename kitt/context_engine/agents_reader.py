from pathlib import Path
from typing import List, Optional

from kitt.security.workspace_fs import WorkspaceFileSystem


class HierarchicalAgentsReader:
    """Discover AGENTS.md from repository root down to a target path safely."""

    def __init__(
        self,
        root_dir: str = ".",
        workspace_fs: WorkspaceFileSystem | None = None,
    ):
        self.root_path = Path(root_dir).resolve()
        self.workspace_fs = workspace_fs or WorkspaceFileSystem(self.root_path)

    def _read_agents(self, rel: str) -> str:
        try:
            text, _ = self.workspace_fs.read_text(rel, max_bytes=512 * 1024)
            return text
        except (FileNotFoundError, IsADirectoryError, PermissionError, ValueError, OSError):
            return ""

    def get_merged_agents_rules(self, target_file_path: Optional[str] = None) -> str:
        agent_paths: List[str] = []
        if self._read_agents("AGENTS.md"):
            agent_paths.append("AGENTS.md")

        if target_file_path:
            try:
                safe_target = self.workspace_fs.relative(target_file_path)
            except PermissionError:
                safe_target = "."
            if safe_target != ".":
                target = Path(safe_target)
                # AGENTS rules are normally queried for files. Walk lexical
                # parents only; every AGENTS read is independently verified by
                # WorkspaceFileSystem so no target symlink can expand scope.
                parent = target.parent
                directories = []
                while str(parent) not in {"", "."}:
                    directories.append(parent)
                    parent = parent.parent
                for directory in reversed(directories):
                    rel = (directory / "AGENTS.md").as_posix()
                    if rel not in agent_paths and self._read_agents(rel):
                        agent_paths.append(rel)

        sections: List[str] = []
        for rel in agent_paths:
            content = self._read_agents(rel)
            if content:
                sections.append(f"--- Instructions from {rel} ---\n{content}\n")
        return "\n".join(sections)
