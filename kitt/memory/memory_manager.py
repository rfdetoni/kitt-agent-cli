import re
from pathlib import Path
from typing import List, Literal
from dataclasses import dataclass, field
from kitt.context_filter.prompt_budget import TokenCounter

@dataclass
class MemoryItem:
    text: str
    scope: Literal['GLOBAL', 'PROJECT']
    priority: int = 1
    tags: List[str] = field(default_factory=list)
    created_at: str = ""

class MemoryManager:
    """Manages persistent project and global memory with structured MemoryItem retrieval."""

    def __init__(self, root_dir: str = ".", persistence_enabled: bool = True):
        self.root_dir = Path(root_dir).resolve()
        self.persistence_enabled = persistence_enabled
        self.project_mem_path = self.root_dir / ".kitt" / "memory" / "project_memory.md"
        self.global_mem_path = Path.home() / ".kitt" / "global_memory.md"

        if persistence_enabled:
            self._ensure_files()

    def _ensure_files(self):
        self.project_mem_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.project_mem_path.exists():
            self.project_mem_path.write_text("# Project Memory & Guidelines\n\n- Write clean, modular, tested code.\n", encoding='utf-8')

        self.global_mem_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.global_mem_path.exists():
            self.global_mem_path.write_text("# K.I.T.T. Global User Preferences\n\n- Prefer standard library and minimalist diffs.\n", encoding='utf-8')

    def add_project_memory(self, note: str):
        if not self.persistence_enabled:
            return
        content = self.project_mem_path.read_text(encoding='utf-8')
        updated = content.rstrip() + f"\n- {note}\n"
        self.project_mem_path.write_text(updated, encoding='utf-8')

    def clear_project_memory(self):
        if not self.persistence_enabled:
            return
        self.project_mem_path.write_text("# Project Memory & Guidelines\n\n", encoding='utf-8')

    def get_items(self) -> List[MemoryItem]:
        items: List[MemoryItem] = []
        if self.global_mem_path.exists():
            for line in self.global_mem_path.read_text(encoding='utf-8', errors='ignore').splitlines():
                line_str = line.strip()
                if line_str.startswith("- "):
                    items.append(MemoryItem(text=line_str[2:], scope='GLOBAL'))

        if self.project_mem_path.exists():
            for line in self.project_mem_path.read_text(encoding='utf-8', errors='ignore').splitlines():
                line_str = line.strip()
                if line_str.startswith("- "):
                    items.append(MemoryItem(text=line_str[2:], scope='PROJECT'))

        return items

    def get_relevant_memories(self, prompt: str) -> List[MemoryItem]:
        words = set(re.findall(r"[a-zA-Z0-9_+-]{4,}", prompt.lower()))
        all_items = self.get_items()
        if not words:
            return sorted(all_items, key=lambda item: item.priority, reverse=True)[:5]

        relevant = []
        for item in all_items:
            item_words = set(re.findall(r"[a-zA-Z0-9_+-]{4,}", item.text.lower()))
            score = len(words.intersection(item_words)) + item.priority
            if words.intersection(item_words):
                relevant.append((score, item))
        return [item for _score, item in sorted(relevant, key=lambda row: row[0], reverse=True)[:8]]

    def get_memory_context(self, prompt: str = "", max_tokens: int = 400) -> str:
        if not prompt:
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
        lines = []
        used = 0
        for item in self.get_relevant_memories(prompt):
            line = f"- [{item.scope}] {item.text}"
            tokens = TokenCounter.count_tokens(line)
            if used + tokens > max_tokens:
                continue
            lines.append(line)
            used += tokens
        return "\n".join(lines)
