"""Host-side deterministic validator for DreamPlan proposals."""
from __future__ import annotations

import re
from typing import Tuple, List, Optional, Set, Dict

from kitt.dreaming.models import (
    DreamOperation,
    DreamPlan,
    DreamSnapshot,
    MemoryRecord,
    MEMORY_KINDS,
    DREAM_OPERATIONS,
)
from kitt.security.sensitive_data import SensitiveDataScanner


class DreamValidator:
    """Validates DreamPlan operations against provenance, safety, secret scanning, and schema constraints."""

    def __init__(
        self,
        min_confidence_commit: float = 0.90,
        min_confidence_candidate: float = 0.70,
        max_content_length: int = 1000,
        scanner: Optional[SensitiveDataScanner] = None,
    ):
        self.min_confidence_commit = min_confidence_commit
        self.min_confidence_candidate = min_confidence_candidate
        self.max_content_length = max_content_length
        self.scanner = scanner or SensitiveDataScanner()

    def validate_plan(
        self,
        plan: DreamPlan,
        snapshot: DreamSnapshot,
    ) -> Tuple[Tuple[DreamOperation, ...], Tuple[Tuple[DreamOperation, str], ...]]:
        """Validates all operations in a DreamPlan. Returns (accepted, rejected_with_reasons)."""
        accepted: List[DreamOperation] = []
        rejected: List[Tuple[DreamOperation, str]] = []

        existing_mem_ids = {m.id: m for m in snapshot.memories}
        existing_entry_ids = {e.entry_id: e for e in snapshot.recent_entries}
        # Also include entry IDs referenced in sessions
        for s in snapshot.recent_sessions:
            for eid in s.entry_ids:
                if eid not in existing_entry_ids:
                    existing_entry_ids[eid] = None  # type: ignore

        for op in plan.operations:
            valid, reason = self._validate_operation(op, existing_mem_ids, existing_entry_ids)
            if valid:
                accepted.append(op)
            else:
                rejected.append((op, reason))

        return tuple(accepted), tuple(rejected)

    def _validate_operation(
        self,
        op: DreamOperation,
        existing_mem_ids: Dict[str, MemoryRecord],
        existing_entry_ids: Dict[str, Any],
    ) -> Tuple[bool, str]:
        # 1. Operation type check
        if op.operation not in DREAM_OPERATIONS:
            return False, f"Invalid operation: {op.operation}"

        if op.operation == "IGNORE":
            return True, "Ignored"

        # 2. Confidence threshold
        if op.confidence < self.min_confidence_candidate:
            return False, f"Confidence {op.confidence:.2f} below candidate threshold {self.min_confidence_candidate:.2f}"

        # 3. Content validations for ADD / MERGE / SUPERSEDE / NORMALIZE
        if op.operation in ("ADD", "MERGE", "SUPERSEDE", "NORMALIZE"):
            if not op.proposed_content or not op.proposed_content.strip():
                return False, "Proposed content is empty"

            content = op.proposed_content.strip()
            if len(content) > self.max_content_length:
                return False, f"Proposed content length ({len(content)}) exceeds maximum {self.max_content_length}"

            if op.proposed_kind not in MEMORY_KINDS:
                return False, f"Invalid memory kind: {op.proposed_kind}"

            # 4. Sensitive data & Secret scanning
            if self._contains_sensitive_data(content):
                return False, "Sensitive data or secret detected in proposed content"

        # 5. Provenance validation for source_memory_ids
        for mid in op.source_memory_ids:
            if mid not in existing_mem_ids:
                return False, f"Referenced memory ID '{mid}' does not exist in snapshot"
            target_mem = existing_mem_ids[mid]
            # Protect pinned memories
            if target_mem.pinned and op.operation in ("SUPERSEDE", "NORMALIZE"):
                return False, f"Cannot modify pinned memory '{mid}' without explicit user directive"

        # 6. Evidence grounding validation for ADD / MERGE / SUPERSEDE
        if op.operation in ("ADD", "MERGE", "SUPERSEDE"):
            if not op.source_entry_ids:
                return False, "Operation missing source_entry_ids evidence"

            for eid in op.source_entry_ids:
                if eid not in existing_entry_ids:
                    return False, f"Referenced entry ID '{eid}' does not exist in snapshot evidence"

            # Check grounding match against entry summary
            if op.proposed_content:
                grounding_found = False
                prop_words = set(re.findall(r'\b\w{3,}\b', op.proposed_content.lower()))
                for eid in op.source_entry_ids:
                    entry = existing_entry_ids.get(eid)
                    if entry and hasattr(entry, "summary_text"):
                        entry_words = set(re.findall(r'\b\w{3,}\b', entry.summary_text.lower()))
                        if prop_words.intersection(entry_words):
                            grounding_found = True
                            break
                    else:
                        grounding_found = True  # referenced via session
                        break

                if not grounding_found:
                    return False, "Proposed content lacks lexical/semantic grounding in referenced evidence entries"

        return True, "Valid"

    def _contains_sensitive_data(self, text: str) -> bool:
        # Check standard patterns (e.g. Bearer, sk-, API keys, tokens)
        secret_patterns = [
            r'sk-[a-zA-Z0-9]{20,}',
            r'Bearer\s+[a-zA-Z0-9_\-\.]{20,}',
            r'(?:api[_-]?key|password|secret|token)\s*[:=]\s*["\']?[a-zA-Z0-9_\-\.]{8,}',
        ]
        for pat in secret_patterns:
            if re.search(pat, text, re.IGNORECASE):
                return True

        if hasattr(self.scanner, "scan"):
            try:
                findings = self.scanner.scan(text)
                if findings:
                    return True
            except Exception:
                pass

        return False
