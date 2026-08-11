import os
from pathlib import Path
from typing import List

class MemoryManager:
    """Manages persistent project and global memory for K.I.T.T. (inspired by OpenClaude memory)."""

    def __init__(self, root_dir: str = "."):
        self.root_dir = Path(root_dir).resolve()
        self.project_mem_path = self.root_dir / ".kitt" / "memory" / "project_memory.md"
        self.global_mem_path = Path.home() / ".kitt" / "global_memory.md"

        self._ensure_files()

    def _ensure_files(self):
        self.project_mem_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.project_mem_path.exists():
            self.project_mem_path.write_text("# Project Memory & Guidelines\n\n- Write clean, modular, tested code.\n", encoding='utf-8')

        self.global_mem_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.global_mem_path.exists():
            self.global_mem_path.write_text("# K.I.T.T. Global User Preferences\n\n- Prefer standard library and minimalist diffs.\n", encoding='utf-8')

    def add_project_memory(self, note: str):
        content = self.project_mem_path.read_text(encoding='utf-8')
        updated = content.rstrip() + f"\n- {note}\n"
        self.project_mem_path.write_text(updated, encoding='utf-8')

    def get_memory_context(self) -> str:
        lines = []
        if self.global_mem_path.exists():
            g_content = self.global_mem_path.read_text(encoding='utf-8', errors='ignore').strip()
            if g_content:
                lines.append(f"--- Global Memory ---\n{g_content}")

        if self.project_mem_path.exists():
            p_content = self.project_mem_path.read_text(encoding='utf-8', errors='ignore').strip()
            if p_content:
                lines.append(f"--- Project Memory ---\n{p_content}")

        return "\n\n".join(lines)

    def clear_project_memory(self):
        self.project_mem_path.write_text("# Project Memory & Guidelines\n\n", encoding='utf-8')
