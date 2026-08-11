from pathlib import Path
from typing import List, Optional

class HierarchicalAgentsReader:
    """Discovers and merges AGENTS.md files from repository root down to target file path."""

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()

    def get_merged_agents_rules(self, target_file_path: Optional[str] = None) -> str:
        agents_files: List[Path] = []

        # 1. Root AGENTS.md
        root_agents = self.root_path / "AGENTS.md"
        if root_agents.exists() and root_agents.is_file():
            agents_files.append(root_agents)

        # 2. Walk directory hierarchy down to target file
        if target_file_path:
            target_p = (self.root_path / target_file_path).resolve()
            if target_p.is_relative_to(self.root_path):
                current = target_p.parent if target_p.is_file() else target_p
                dirs_chain = []
                while current != self.root_path and current.is_relative_to(self.root_path):
                    dirs_chain.append(current)
                    current = current.parent

                # Reverse so root-most subdirectory comes first, closest directory comes last
                for d in reversed(dirs_chain):
                    sub_agents = d / "AGENTS.md"
                    if sub_agents.exists() and sub_agents.is_file() and sub_agents not in agents_files:
                        agents_files.append(sub_agents)

        if not agents_files:
            return ""

        rule_sections: List[str] = []
        for af in agents_files:
            try:
                rel = af.relative_to(self.root_path)
                content = af.read_text(encoding='utf-8', errors='ignore')
                rule_sections.append(f"--- Instructions from {rel} ---\n{content}\n")
            except Exception:
                continue

        return "\n".join(rule_sections)
