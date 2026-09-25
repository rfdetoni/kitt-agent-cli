from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvidenceState(StrEnum):
    PRESENT = "PRESENT"
    WIRED = "WIRED"
    EXERCISED = "EXERCISED"
    OUTCOME_SUPPORTED = "OUTCOME_SUPPORTED"
    MISSING = "MISSING"
    UNOBSERVED = "UNOBSERVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


EVIDENCE_STRENGTH = {
    EvidenceState.MISSING: 0,
    EvidenceState.UNOBSERVED: 0,
    EvidenceState.NOT_APPLICABLE: 0,
    EvidenceState.PRESENT: 1,
    EvidenceState.WIRED: 2,
    EvidenceState.EXERCISED: 3,
    EvidenceState.OUTCOME_SUPPORTED: 4,
}


@dataclass(frozen=True)
class SessionEventRecord:
    id: str
    conversation_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    payload_hash: str
    created_at: float
    turn_id: str | None = None
    episode_id: str | None = None
    model_visible: bool = False
    replayable: bool = True


@dataclass(frozen=True)
class TaskEpisode:
    id: str
    conversation_id: str
    objective: str
    acceptance: tuple[str, ...]
    state: str
    started_at: float
    goal_id: str | None = None
    completed_at: float | None = None
    outcome: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    episode_id: str
    dimension: str
    check_id: str
    state: EvidenceState
    result: str
    evidence_refs: tuple[str, ...]
    finding_refs: tuple[str, ...]
    created_at: float


@dataclass(frozen=True)
class InvariantResult:
    name: str
    ok: bool
    detail: str = ""
    critical: bool = False
