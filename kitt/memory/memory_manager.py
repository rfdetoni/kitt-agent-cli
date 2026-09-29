"""Agent-facing facade backed exclusively by kitt-memoryd."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Literal

from kitt.context_filter.prompt_budget import TokenCounter
from kitt.memory.shared_client import KittMemoryClient


@dataclass
class MemoryItem:
    text: str
    scope: Literal["GLOBAL", "PROJECT"]
    priority: int = 1
    tags: List[str] = field(default_factory=list)
    created_at: str = ""


class MemoryManager:
    def __init__(self, root_dir: str = ".", persistence_enabled: bool = True, memory_repo: Any = None,
                 workspace_id: str | None = None, shared_client: Any = None):
        del root_dir
        self.persistence_enabled = persistence_enabled
        self.workspace_id = workspace_id or "default"
        self.client: KittMemoryClient = shared_client or KittMemoryClient()
        self.memory_repo = memory_repo

    def add_project_memory(self, note: str, kind: str = "PROJECT_RULE", pinned: bool = True) -> None:
        if not self.persistence_enabled:
            return
        note = str(note or "").strip()
        if note:
            self.client.remember(self.workspace_id, note, kind=kind, pinned=pinned)

    def clear_project_memory(self) -> None:
        if self.persistence_enabled:
            self.client.manage("archive_workspace", {"namespace": "agent-cli", "workspace_id": self.workspace_id})


    def remember_correction(
        self,
        context: str,
        predicted: str,
        corrected: str,
        reason: str | None = None,
        source: str = "agent",
    ) -> str:
        body = self.client.manage("correction.record", {
            "namespace": "agent-cli",
            "workspace_id": self.workspace_id,
            "context": context,
            "predicted": predicted,
            "corrected": corrected,
            "reason": reason,
            "source": source,
            "sensitivity": "private",
        })
        correction = body.get("correction")
        if not isinstance(correction, dict) or not correction.get("id"):
            raise RuntimeError("kitt-memoryd did not return a correction id")
        return str(correction["id"])

    def remember_concept(
        self,
        name: str,
        definition: str,
        confidence: float = 0.7,
        labels=(),
        source_memory_ids=(),
    ) -> dict[str, Any]:
        body = self.client.manage("concept.upsert", {
            "namespace": "agent-cli",
            "workspace_id": self.workspace_id,
            "name": name,
            "definition": definition,
            "confidence": float(confidence),
            "labels": [str(value) for value in labels],
            "source_memory_ids": [str(value) for value in source_memory_ids],
            "sensitivity": "private",
        })
        concept = body.get("concept")
        if not isinstance(concept, dict):
            raise RuntimeError("kitt-memoryd did not return a concept")
        return concept

    def link_concepts(
        self,
        source_id: str,
        target_id: str,
        relation: str = "RELATED",
        weight: float = 1.0,
    ) -> str:
        body = self.client.manage("concept.link", {
            "namespace": "agent-cli",
            "workspace_id": self.workspace_id,
            "source_id": source_id,
            "target_id": target_id,
            "relation": relation,
            "weight": float(weight),
        })
        edge = body.get("edge")
        if not isinstance(edge, dict) or not edge.get("id"):
            raise RuntimeError("kitt-memoryd did not return a knowledge-link id")
        return str(edge["id"])

    @staticmethod
    def _item(record: dict[str, Any]) -> MemoryItem | None:
        text = str(record.get("content") or "").strip()
        if not text:
            return None
        scope = "GLOBAL" if str(record.get("scope") or "").lower() == "global" else "PROJECT"
        priority = 3 if record.get("pinned") else 2
        return MemoryItem(text=text, scope=scope, priority=priority)

    def get_items(self) -> List[MemoryItem]:
        if not self.persistence_enabled:
            return []
        body = self.client.manage("list", {
            "namespace": "agent-cli", "workspace_id": self.workspace_id, "status": "ACTIVE", "limit": 512
        })
        result: list[MemoryItem] = []
        for row in body.get("records", []):
            if isinstance(row, dict):
                item = self._item(row)
                if item:
                    result.append(item)
        return result

    def get_relevant_memories(self, prompt: str) -> List[MemoryItem]:
        if not self.persistence_enabled:
            return []
        rows = self.client.recall(self.workspace_id, prompt, limit=8)
        result: list[MemoryItem] = []
        for row in rows:
            item = self._item(row)
            if item:
                result.append(item)
        return result

    def get_memory_context(self, prompt: str = "", max_tokens: int = 400) -> str:
        items = self.get_relevant_memories(prompt) if prompt else self.get_items()
        if not prompt:
            body = "\n".join(f"- {item.text}" for item in items) if items else "(empty)"
            return f"--- Project Memory ---\n{body}"
        lines: list[str] = []
        used = 0
        for item in items:
            line = f"- [{item.scope}] {item.text}"
            cost = TokenCounter.count_tokens(line)
            if used + cost <= max_tokens:
                lines.append(line)
                used += cost
        return "\n".join(lines)
