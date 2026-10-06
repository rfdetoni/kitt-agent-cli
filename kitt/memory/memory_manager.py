"""Agent-facing facade backed exclusively by kitt-memoryd."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Literal
import hashlib
import json
import time
from contextlib import nullcontext

from kitt.context_filter.prompt_budget import TokenCounter
from kitt.memory.shared_client import KittMemoryClient


@dataclass
class MemoryItem:
    text: str
    scope: Literal["GLOBAL", "PROJECT"]
    priority: int = 1
    tags: List[str] = field(default_factory=list)
    created_at: str = ""
    memory_id: str = ""
    recall_trace_id: str = ""


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


    def lifecycle_event(
        self,
        event: str,
        *,
        source_id: str,
        source_revision: str,
        evidence: Any = None,
        source_kind: str = "agent",
    ) -> dict[str, Any]:
        """Submit lifecycle evidence by digest; raw evidence is never persisted."""
        canonical = json.dumps(
            evidence if evidence is not None else {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return self.client.lifecycle(
            event,
            workspace_id=self.workspace_id,
            source_id=source_id,
            source_revision=source_revision,
            input_digest=digest,
            source_kind=source_kind,
            namespace="agent-cli",
        )


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
    def _item(record: dict[str, Any], recall_trace_id: str = "") -> MemoryItem | None:
        text = str(record.get("content") or "").strip()
        if not text:
            return None
        scope: Literal["GLOBAL", "PROJECT"] = (
            "GLOBAL"
            if str(record.get("scope") or "").lower() == "global"
            else "PROJECT"
        )
        priority = 3 if record.get("pinned") else 2
        return MemoryItem(
            text=text,
            scope=scope,
            priority=priority,
            memory_id=str(record.get("id") or ""),
            recall_trace_id=str(recall_trace_id or ""),
        )

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

    def get_relevant_memories(
        self,
        prompt: str,
        *,
        max_tokens: int = 400,
    ) -> List[MemoryItem]:
        if not self.persistence_enabled:
            return []
        budget = max(32, min(int(max_tokens), 8_192))
        hits, search_trace_id = self.client.search(
            self.workspace_id,
            prompt,
            max_results=24,
            token_budget=max(32, budget // 2),
        )
        ids = [
            str(hit.get("id") or "")
            for hit in hits
            if isinstance(hit, dict) and str(hit.get("id") or "")
        ]
        hydrated, get_trace_id, truncated_ids = self.client.get(
            self.workspace_id,
            ids,
            token_budget=budget,
            include_provenance=False,
        )
        result: list[MemoryItem] = []
        hydrated_ids: set[str] = set()
        for wrapper in hydrated:
            if not isinstance(wrapper, dict):
                continue
            record = wrapper.get("record")
            if not isinstance(record, dict):
                continue
            item = self._item(record, get_trace_id)
            if item:
                hydrated_ids.add(item.memory_id)
                result.append(item)

        # Keep a bounded candidate snippet when full hydration does not fit.
        truncated = {str(value) for value in truncated_ids}
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            memory_id = str(hit.get("id") or "")
            if not memory_id or memory_id in hydrated_ids:
                continue
            if memory_id not in truncated and hydrated_ids:
                continue
            snippet = str(hit.get("snippet") or "").strip()
            if not snippet:
                continue
            scope: Literal["GLOBAL", "PROJECT"] = (
                "GLOBAL"
                if str(hit.get("scope") or "").lower() == "global"
                else "PROJECT"
            )
            result.append(
                MemoryItem(
                    text=snippet,
                    scope=scope,
                    priority=2,
                    memory_id=memory_id,
                    recall_trace_id=search_trace_id,
                )
            )
        return result

    def _record_presented(
        self,
        items: List[MemoryItem],
        *,
        turn_id: str,
        purpose: str,
    ) -> None:
        if not turn_id:
            return
        now = int(time.time())
        receipts = [{
            "recall_trace_id": item.recall_trace_id, "memory_id": item.memory_id,
            "consumer": "kitt-agent-cli", "purpose": purpose, "presented": True,
            "referenced": False, "used_for_action": False, "outcome": "",
            "turn_id": turn_id, "consumed_at": now,
        } for item in items if item.memory_id and item.recall_trace_id][:128]
        if receipts:
            try:
                with self.client.request_budget(1.0) if isinstance(self.client, KittMemoryClient) else nullcontext():
                    self.client.manage("receipt.record_batch", {"receipts": receipts})
            except Exception:
                # Telemetry is bounded and cannot invalidate a successful recall.
                pass

    def get_memory_context(
        self,
        prompt: str = "",
        max_tokens: int = 400,
        *,
        turn_id: str = "",
    ) -> str:
        with self.client.request_budget(8.0) if isinstance(self.client, KittMemoryClient) else nullcontext():
            return self._get_memory_context(prompt, max_tokens, turn_id=turn_id)

    def _get_memory_context(self, prompt: str, max_tokens: int, *, turn_id: str) -> str:
        items = (
            self.get_relevant_memories(prompt, max_tokens=max_tokens)
            if prompt
            else self.get_items()
        )
        if not prompt:
            body = "\n".join(f"- {item.text}" for item in items) if items else "(empty)"
            return f"--- Project Memory ---\n{body}"
        lines: list[str] = []
        selected: list[MemoryItem] = []
        used = 0
        for item in items:
            line = f"- [{item.scope}] {item.text}"
            cost = TokenCounter.count_tokens(line)
            if used + cost <= max_tokens:
                lines.append(line)
                selected.append(item)
                used += cost
        self._record_presented(
            selected,
            turn_id=turn_id,
            purpose="context-envelope",
        )
        return "\n".join(lines)
