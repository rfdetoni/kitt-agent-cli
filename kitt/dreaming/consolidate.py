"""Phase 3: CONSOLIDATE — Small model semantic consolidation and deterministic fallback."""
from __future__ import annotations

import json
import re
from typing import List, Tuple, Optional, Dict, Any

from kitt.dreaming.models import (
    CandidateSignal,
    DreamOperation,
    DreamPlan,
    DreamSnapshot,
    MemoryRecord,
)
from kitt.llm.client import LLMClient
from kitt.security.egress import EgressPolicy

CONSOLIDATION_SYSTEM_PROMPT = """You are a strict memory consolidation classifier for an AI agent's long-term memory.
Your task is to analyze candidate signals from recent sessions alongside existing memories, and output consolidation operations.

RULES:
1. Do not answer the user or write code.
2. Return ONLY a valid JSON object matching the schema below.
3. Allowed operations:
   - "ADD": New durable memory backed by evidence.
   - "KEEP": Existing memory remains active and unchanged.
   - "MERGE": Merge two or more memories/signals into one clearer memory.
   - "SUPERSEDE": Replace an older superseded memory with a newer updated fact (specify source_memory_ids of old memory).
   - "NORMALIZE": Clean up wording of an existing memory.
   - "IGNORE": Transient noise, greeting, or irrelevant temporary detail.
4. Allowed kinds: USER_PREFERENCE, PROJECT_RULE, ARCHITECTURE_DECISION, TECHNICAL_FACT, WORKING_PATTERN, FAILED_APPROACH, OPEN_ISSUE, PROJECT_STATE.
5. Every operation must cite source_entry_ids and source_memory_ids where applicable.

JSON SCHEMA:
{
  "operations": [
    {
      "operation": "ADD" | "KEEP" | "MERGE" | "SUPERSEDE" | "NORMALIZE" | "IGNORE",
      "source_memory_ids": ["mem_..."],
      "source_entry_ids": ["entry_..."],
      "proposed_kind": "USER_PREFERENCE" | "PROJECT_RULE" | "ARCHITECTURE_DECISION" | "TECHNICAL_FACT" | "WORKING_PATTERN" | "FAILED_APPROACH" | "OPEN_ISSUE" | "PROJECT_STATE",
      "proposed_content": "Exact normalized memory text",
      "confidence": 0.95,
      "reason_code": "NEWER_FACT" | "DUPLICATE" | "REPEATED_PREFERENCE" | "TEMPORAL_UPDATE" | "FAILED_APPROACH"
    }
  ]
}"""


class DreamConsolidatePhase:
    """Produces a DreamPlan through semantic LLM classification or deterministic fallback."""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        egress_policy: Optional[EgressPolicy] = None,
        model_name: str = "",
    ):
        self.llm_client = llm_client
        self.egress_policy = egress_policy
        if model_name:
            self.model_name = model_name
        elif llm_client and getattr(llm_client, "profile", None):
            self.model_name = f"{llm_client.profile.backend}/{llm_client.profile.model}"
        else:
            self.model_name = "context-gather"

    def consolidate(
        self,
        snapshot: DreamSnapshot,
        signals: Tuple[CandidateSignal, ...],
    ) -> DreamPlan:
        """Consolidates candidate signals and existing memories into an actionable DreamPlan."""
        if not signals:
            return DreamPlan(operations=())

        # 1. Try semantic consolidation if client is provided and allowed
        if self.llm_client and self._can_use_llm():
            try:
                plan = self._semantic_consolidate(snapshot, signals)
                if plan and plan.operations:
                    return plan
            except Exception:
                pass  # Fall back to deterministic consolidation

        # 2. Deterministic Fallback Consolidation
        return self._deterministic_consolidate(snapshot, signals)

    def _can_use_llm(self) -> bool:
        if not self.egress_policy:
            return True
        # If egress policy is offline or local_only, verify client backend is local
        if self.egress_policy.mode in ("offline", "local_only"):
            backend = getattr(getattr(self.llm_client, "profile", None), "backend", "").lower()
            if backend not in ("ollama", "lmstudio", "localai", "vllm"):
                return False
        return True

    def _semantic_consolidate(
        self,
        snapshot: DreamSnapshot,
        signals: Tuple[CandidateSignal, ...],
    ) -> Optional[DreamPlan]:
        existing_memories_payload = [
            {
                "id": m.id,
                "kind": m.kind,
                "content": m.content,
                "status": m.status,
                "pinned": m.pinned,
            }
            for m in snapshot.memories if m.status in ("ACTIVE", "CANDIDATE")
        ]

        candidate_signals_payload = [
            {
                "id": s.id,
                "conversation_id": s.conversation_id,
                "source_entry_ids": list(s.source_entry_ids),
                "kind_hint": s.kind_hint,
                "content": s.normalized_content,
            }
            for s in signals
        ]

        prompt_data = {
            "existing_memories": existing_memories_payload,
            "candidate_signals": candidate_signals_payload,
        }

        messages = [
            {"role": "user", "content": json.dumps(prompt_data, indent=2)}
        ]

        raw_response = self.llm_client.chat(messages, system_prompt=CONSOLIDATION_SYSTEM_PROMPT, response_format="json")
        data = self._parse_json_robust(raw_response)
        if not data or not isinstance(data.get("operations"), list):
            return None

        ops: List[DreamOperation] = []
        for item in data["operations"]:
            if not isinstance(item, dict):
                continue
            op_name = str(item.get("operation", "IGNORE")).upper()
            if op_name not in ("ADD", "KEEP", "MERGE", "SUPERSEDE", "NORMALIZE", "IGNORE"):
                continue

            ops.append(
                DreamOperation(
                    operation=op_name,  # type: ignore
                    source_memory_ids=tuple(item.get("source_memory_ids") or ()),
                    source_entry_ids=tuple(item.get("source_entry_ids") or ()),
                    proposed_kind=item.get("proposed_kind"),
                    proposed_content=item.get("proposed_content"),
                    confidence=float(item.get("confidence", 0.8)),
                    reason_code=str(item.get("reason_code", "")),
                )
            )

        return DreamPlan(operations=tuple(ops))

    def _deterministic_consolidate(
        self,
        snapshot: DreamSnapshot,
        signals: Tuple[CandidateSignal, ...],
    ) -> DreamPlan:
        ops: List[DreamOperation] = []
        existing_hashes = {m.content_hash: m for m in snapshot.memories if m.status == "ACTIVE"}

        for sig in signals:
            # 1. Exact duplicate check
            if sig.content_hash in existing_hashes:
                existing = existing_hashes[sig.content_hash]
                ops.append(
                    DreamOperation(
                        operation="KEEP",
                        source_memory_ids=(existing.id,),
                        source_entry_ids=sig.source_entry_ids,
                        proposed_kind=existing.kind,
                        proposed_content=existing.content,
                        confidence=1.0,
                        reason_code="DUPLICATE",
                    )
                )
                continue

            # 2. Check for contradiction / temporal supersession
            # E.g. if an existing memory has the same kind and high semantic overlap
            superseded_mem = None
            for m in snapshot.memories:
                if m.status == "ACTIVE" and m.kind == sig.kind_hint and not m.pinned:
                    m_words = set(re.findall(r'\b\w{4,}\b', m.content.lower()))
                    s_words = set(re.findall(r'\b\w{4,}\b', sig.normalized_content.lower()))
                    overlap = len(m_words.intersection(s_words)) / max(1, len(m_words))
                    if overlap >= 0.75:
                        superseded_mem = m
                        break

            if superseded_mem:
                ops.append(
                    DreamOperation(
                        operation="SUPERSEDE",
                        source_memory_ids=(superseded_mem.id,),
                        source_entry_ids=sig.source_entry_ids,
                        proposed_kind=sig.kind_hint or superseded_mem.kind,
                        proposed_content=sig.normalized_content,
                        confidence=sig.deterministic_score,
                        reason_code="NEWER_FACT",
                    )
                )
            else:
                ops.append(
                    DreamOperation(
                        operation="ADD",
                        source_memory_ids=(),
                        source_entry_ids=sig.source_entry_ids,
                        proposed_kind=sig.kind_hint or "TECHNICAL_FACT",
                        proposed_content=sig.normalized_content,
                        confidence=sig.deterministic_score,
                        reason_code="NEWER_FACT",
                    )
                )

        return DreamPlan(operations=tuple(ops))

    @staticmethod
    def _parse_json_robust(text: str) -> Optional[Dict[str, Any]]:
        clean = text.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\n", "", clean)
            clean = re.sub(r"\n```$", "", clean).strip()
        try:
            return json.loads(clean)
        except Exception:
            m = re.search(r"(\{.*\})", clean, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass
        return None
