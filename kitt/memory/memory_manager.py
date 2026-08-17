"""Manages persistent project and global memory with structured MemoryItem retrieval."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Literal, Optional, Any
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

    def __init__(
        self,
        root_dir: str = ".",
        persistence_enabled: bool = True,
        memory_repo: Optional[Any] = None,
        workspace_id: Optional[str] = None,
    ):
        self.root_dir = Path(root_dir).resolve()
        self.persistence_enabled = persistence_enabled
        self.memory_repo = memory_repo
        self.workspace_id = workspace_id or "default"
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

    def add_project_memory(self, note: str, kind: str = "PROJECT_RULE", pinned: bool = True):
        if not self.persistence_enabled:
            return
        # 1. Update SQLite relational repository if available
        if self.memory_repo and self.workspace_id:
            try:
                self.memory_repo.add_direct_memory(self.workspace_id, note, kind=kind, pinned=pinned)
            except Exception:
                pass

        # 2. Update local markdown file
        content = self.project_mem_path.read_text(encoding='utf-8', errors='ignore') if self.project_mem_path.exists() else ""
        updated = content.rstrip() + f"\n- {note}\n"
        self.project_mem_path.write_text(updated, encoding='utf-8')

    def clear_project_memory(self):
        if not self.persistence_enabled:
            return
        self.project_mem_path.write_text("# Project Memory & Guidelines\n\n", encoding='utf-8')

    def get_items(self) -> List[MemoryItem]:
        items: List[MemoryItem] = []
        seen_texts = set()

        # 1. Read from canonical SQLite MemoryRepository if present
        if self.memory_repo and self.workspace_id:
            try:
                records = self.memory_repo.get_active_memories(self.workspace_id)
                for rec in records:
                    clean = rec.content.strip()
                    if clean not in seen_texts:
                        seen_texts.add(clean)
                        prio = 3 if rec.pinned else 2 if rec.kind in ("PROJECT_RULE", "ARCHITECTURE_DECISION") else 1
                        items.append(MemoryItem(text=clean, scope='PROJECT', priority=prio))
            except Exception:
                pass

        # 2. Read from global memory markdown
        if self.global_mem_path.exists():
            for line in self.global_mem_path.read_text(encoding='utf-8', errors='ignore').splitlines():
                line_str = line.strip()
                if line_str.startswith("- "):
                    t = line_str[2:].strip()
                    if t not in seen_texts:
                        seen_texts.add(t)
                        items.append(MemoryItem(text=t, scope='GLOBAL', priority=2))

        # 3. Read from project memory markdown
        if self.project_mem_path.exists():
            for line in self.project_mem_path.read_text(encoding='utf-8', errors='ignore').splitlines():
                line_str = line.strip()
                if line_str.startswith("- "):
                    t = line_str[2:].strip()
                    if t not in seen_texts:
                        seen_texts.add(t)
                        items.append(MemoryItem(text=t, scope='PROJECT', priority=1))

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
            if words.intersection(item_words) or item.priority >= 2:
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
