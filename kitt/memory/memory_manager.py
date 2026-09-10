"""Persistent memory facade with one authoritative structured backend at a time."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Literal, Optional

from kitt.context_filter.prompt_budget import TokenCounter
from kitt.memory.shared_client import SharedMemoryClient, SharedMemoryUnavailable


@dataclass
class MemoryItem:
    text: str
    scope: Literal["GLOBAL", "PROJECT"]
    priority: int = 1
    tags: List[str] = field(default_factory=list)
    created_at: str = ""


class MemoryManager:
    """Prefer shared kittd memory; fall back to one local backend when unavailable."""

    def __init__(
        self,
        root_dir: str = ".",
        persistence_enabled: bool = True,
        memory_repo: Optional[Any] = None,
        workspace_id: Optional[str] = None,
        shared_client: Optional[Any] = None,
    ):
        self.root_dir = Path(root_dir).resolve()
        self.persistence_enabled = persistence_enabled
        self.memory_repo = memory_repo
        self.workspace_id = workspace_id or "default"
        self.shared_client = shared_client
        state_home = self.root_dir / ".kitt"
        self.project_mem_path = (
            state_home
            / "workspaces"
            / self.workspace_id
            / "memory"
            / "project_memory.md"
        )
        self.global_mem_path = state_home / "global_memory.md"

        if persistence_enabled:
            self._ensure_files()

    def _ensure_files(self) -> None:
        self.project_mem_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.project_mem_path.exists():
            self.project_mem_path.write_text(
                "# Project Memory & Guidelines\n\n- Write clean, modular, tested code.\n",
                encoding="utf-8",
            )
        self.global_mem_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.global_mem_path.exists():
            self.global_mem_path.write_text(
                "# K.I.T.T. Global User Preferences\n\n"
                "- Prefer standard library and minimalist diffs.\n",
                encoding="utf-8",
            )

    def _shared(self) -> Any:
        if self.shared_client is None:
            self.shared_client = SharedMemoryClient()
        return self.shared_client

    def add_project_memory(
        self,
        note: str,
        kind: str = "PROJECT_RULE",
        pinned: bool = True,
    ) -> None:
        if not self.persistence_enabled:
            return
        note = note.strip()
        if not note:
            return

        try:
            self._shared().remember(self.workspace_id, note, kind=kind, pinned=pinned)
            return
        except SharedMemoryUnavailable:
            pass

        if self.memory_repo is not None:
            try:
                self.memory_repo.add_direct_memory(
                    self.workspace_id,
                    note,
                    kind=kind,
                    pinned=pinned,
                )
                return
            except Exception:
                pass

        self._append_markdown(note)

    def _append_markdown(self, note: str) -> None:
        content = (
            self.project_mem_path.read_text(encoding="utf-8", errors="ignore")
            if self.project_mem_path.exists()
            else ""
        )
        existing = {
            line.strip()[2:].strip()
            for line in content.splitlines()
            if line.strip().startswith("- ")
        }
        if note in existing:
            return
        self.project_mem_path.write_text(
            content.rstrip() + f"\n- {note}\n",
            encoding="utf-8",
        )

    def clear_project_memory(self) -> None:
        if self.persistence_enabled:
            self.project_mem_path.write_text(
                "# Project Memory & Guidelines\n\n",
                encoding="utf-8",
            )

    def get_items(self) -> List[MemoryItem]:
        items: List[MemoryItem] = []
        seen_texts: set[str] = set()

        if self.memory_repo is not None:
            try:
                records = self.memory_repo.get_active_memories(self.workspace_id)
                for rec in records:
                    clean = rec.content.strip()
                    if clean and clean not in seen_texts:
                        seen_texts.add(clean)
                        priority = (
                            3
                            if rec.pinned
                            else 2
                            if rec.kind in ("PROJECT_RULE", "ARCHITECTURE_DECISION")
                            else 1
                        )
                        items.append(MemoryItem(clean, "PROJECT", priority))
                return items
            except Exception:
                pass

        for path, scope, priority in (
            (self.global_mem_path, "GLOBAL", 2),
            (self.project_mem_path, "PROJECT", 1),
        ):
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if not stripped.startswith("- "):
                    continue
                text = stripped[2:].strip()
                if text and text not in seen_texts:
                    seen_texts.add(text)
                    items.append(MemoryItem(text, scope, priority))  # type: ignore[arg-type]
        return items

    def get_relevant_memories(self, prompt: str) -> List[MemoryItem]:
        if prompt:
            try:
                records = self._shared().recall(self.workspace_id, prompt, limit=8)
                items = [
                    MemoryItem(
                        text=str(record.get("content", "")).strip(),
                        scope="PROJECT",
                        priority=3 if record.get("pinned") else 2,
                    )
                    for record in records
                    if str(record.get("content", "")).strip()
                ]
                return self._rank(prompt, items)
            except SharedMemoryUnavailable:
                pass
        return self._rank(prompt, self.get_items())

    @staticmethod
    def _rank(prompt: str, items: List[MemoryItem]) -> List[MemoryItem]:
        words = set(re.findall(r"[a-zA-Z0-9_+-]{4,}", prompt.lower()))
        if not words:
            return sorted(items, key=lambda item: item.priority, reverse=True)[:5]

        relevant: list[tuple[int, MemoryItem]] = []
        for item in items:
            item_words = set(re.findall(r"[a-zA-Z0-9_+-]{4,}", item.text.lower()))
            overlap = words.intersection(item_words)
            score = len(overlap) + item.priority
            if overlap or item.priority >= 2:
                relevant.append((score, item))
        relevant.sort(key=lambda row: row[0], reverse=True)
        return [item for _, item in relevant[:8]]

    def get_memory_context(self, prompt: str = "", max_tokens: int = 400) -> str:
        if not prompt:
            lines = []
            if self.global_mem_path.exists():
                g_content = self.global_mem_path.read_text(encoding="utf-8", errors="ignore").strip()
                if g_content:
                    lines.append(f"--- Global Memory ---\n{g_content}")
            if self.project_mem_path.exists():
                p_content = self.project_mem_path.read_text(encoding="utf-8", errors="ignore").strip()
                if p_content:
                    lines.append(f"--- Project Memory ---\n{p_content}")
            if not lines and self.get_items():
                project_items = [f"- {item.text}" for item in self.get_items() if item.scope == "PROJECT"]
                global_items = [f"- {item.text}" for item in self.get_items() if item.scope == "GLOBAL"]
                if global_items:
                    lines.append("--- Global Memory ---\n" + "\n".join(global_items))
                if project_items:
                    lines.append("--- Project Memory ---\n" + "\n".join(project_items))
            return "\n\n".join(lines)

        items = self.get_relevant_memories(prompt)
        lines: list[str] = []
        used = 0
        for item in items:
            line = f"- [{item.scope}] {item.text}"
            tokens = TokenCounter.count_tokens(line)
            if used + tokens > max_tokens:
                continue
            lines.append(line)
            used += tokens
        return "\n".join(lines)
