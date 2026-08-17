"""Dreaming Mode domain models, enums, and immutable dataclasses."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Literal, Tuple, Optional, List, Dict, Any

MemoryKind = Literal[
    "USER_PREFERENCE",
    "PROJECT_RULE",
    "ARCHITECTURE_DECISION",
    "TECHNICAL_FACT",
    "WORKING_PATTERN",
    "FAILED_APPROACH",
    "OPEN_ISSUE",
    "PROJECT_STATE",
]

MEMORY_KINDS = {
    "USER_PREFERENCE",
    "PROJECT_RULE",
    "ARCHITECTURE_DECISION",
    "TECHNICAL_FACT",
    "WORKING_PATTERN",
    "FAILED_APPROACH",
    "OPEN_ISSUE",
    "PROJECT_STATE",
}

MemoryStatus = Literal["ACTIVE", "SUPERSEDED", "ARCHIVED", "CANDIDATE"]

MEMORY_STATUSES = {"ACTIVE", "SUPERSEDED", "ARCHIVED", "CANDIDATE"}

DreamOperationType = Literal["ADD", "KEEP", "MERGE", "SUPERSEDE", "NORMALIZE", "IGNORE"]

DREAM_OPERATIONS = {"ADD", "KEEP", "MERGE", "SUPERSEDE", "NORMALIZE", "IGNORE"}


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    workspace_id: str
    kind: MemoryKind
    content: str
    normalized_content: str
    status: MemoryStatus
    importance: float
    confidence: float
    created_at: float
    updated_at: float
    last_accessed_at: Optional[float] = None
    access_count: int = 0
    valid_from: Optional[float] = None
    valid_until: Optional[float] = None
    supersedes_id: Optional[str] = None
    content_hash: str = ""
    pinned: bool = False
    metadata_json: str = "{}"

    def __post_init__(self):
        if self.kind not in MEMORY_KINDS:
            raise ValueError(f"Invalid memory kind: {self.kind!r}")
        if self.status not in MEMORY_STATUSES:
            raise ValueError(f"Invalid memory status: {self.status!r}")
        if not (0.0 <= self.importance <= 1.0):
            raise ValueError(f"Importance must be 0.0..1.0, got {self.importance}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Confidence must be 0.0..1.0, got {self.confidence}")
        if not self.content_hash:
            calculated_hash = hashlib.sha256(self.normalized_content.strip().lower().encode("utf-8")).hexdigest()
            object.__setattr__(self, "content_hash", calculated_hash)


@dataclass(frozen=True)
class MemoryEvidence:
    id: str
    memory_id: str
    workspace_id: str
    session_entry_id: Optional[str]
    conversation_id: Optional[str]
    source_kind: str  # "turn_user" | "turn_assistant" | "command_remember" | "decision" | "compaction"
    evidence_text: str
    created_at: float


@dataclass(frozen=True)
class SessionEntryDigest:
    entry_id: str
    conversation_id: str
    turn_id: Optional[str]
    entry_type: str
    summary_text: str
    created_at: float


@dataclass(frozen=True)
class SessionDigest:
    conversation_id: str
    started_at: float
    completed_at: Optional[float]
    user_requests: Tuple[str, ...]
    decisions: Tuple[str, ...]
    failures: Tuple[str, ...]
    validations: Tuple[str, ...]
    changed_files: Tuple[str, ...]
    entry_ids: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DreamSnapshot:
    workspace_id: str
    memories: Tuple[MemoryRecord, ...]
    recent_sessions: Tuple[SessionDigest, ...]
    recent_entries: Tuple[SessionEntryDigest, ...]
    last_dream_at: Optional[float]
    completed_sessions_since_last_dream: int
    generated_at: float


@dataclass(frozen=True)
class CandidateSignal:
    id: str
    source_entry_ids: Tuple[str, ...]
    conversation_id: str
    kind_hint: Optional[MemoryKind]
    raw_content: str
    normalized_content: str
    occurred_at: float
    deterministic_score: float
    content_hash: str = ""

    def __post_init__(self):
        if not self.content_hash:
            calculated_hash = hashlib.sha256(self.normalized_content.strip().lower().encode("utf-8")).hexdigest()
            object.__setattr__(self, "content_hash", calculated_hash)


@dataclass(frozen=True)
class DreamOperation:
    operation: DreamOperationType
    source_memory_ids: Tuple[str, ...]
    source_entry_ids: Tuple[str, ...]
    proposed_kind: Optional[MemoryKind]
    proposed_content: Optional[str]
    confidence: float
    reason_code: str = ""  # "DUPLICATE" | "NEWER_FACT" | "REPEATED_PREFERENCE" | "TEMPORAL_UPDATE" | "FAILED_APPROACH"

    def __post_init__(self):
        if self.operation not in DREAM_OPERATIONS:
            raise ValueError(f"Invalid dream operation: {self.operation!r}")
        if self.proposed_kind and self.proposed_kind not in MEMORY_KINDS:
            raise ValueError(f"Invalid proposed kind: {self.proposed_kind!r}")


@dataclass(frozen=True)
class DreamPlan:
    operations: Tuple[DreamOperation, ...]
    created_at: float = field(default_factory=time.time)
    dream_version: str = "dream-v1"


@dataclass(frozen=True)
class DreamRun:
    id: str
    workspace_id: str
    started_at: float
    finished_at: Optional[float]
    status: str  # "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"
    sessions_scanned: int
    entries_scanned: int
    signals_found: int
    memories_added: int
    memories_merged: int
    memories_superseded: int
    memories_archived: int
    model: str
    input_tokens: int
    output_tokens: int
    failure_reason: Optional[str] = None
    dry_run: bool = False


@dataclass(frozen=True)
class DreamResult:
    run: DreamRun
    plan: DreamPlan
    snapshot: DreamSnapshot
    accepted_operations: Tuple[DreamOperation, ...]
    rejected_operations: Tuple[Tuple[DreamOperation, str], ...]
