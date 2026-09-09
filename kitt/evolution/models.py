"""Data model for KITT offline skill self-evolution."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class EvolutionStatus(StrEnum):
    PREPARING = "PREPARING"
    OPTIMIZING = "OPTIMIZING"
    EVALUATING = "EVALUATING"
    STAGED = "STAGED"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class EvalCase:
    id: str
    task: str
    rubric: str
    source: str
    split: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def create(
        task: str,
        rubric: str,
        source: str,
        split: str = "train",
        metadata: dict[str, Any] | None = None,
    ) -> "EvalCase":
        payload = f"{source}\0{task}\0{rubric}".encode("utf-8")
        return EvalCase(
            id="case_" + hashlib.sha256(payload).hexdigest()[:16],
            task=task.strip(),
            rubric=rubric.strip(),
            source=source,
            split=split,
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class TrialScore:
    correctness: float
    procedure_following: float
    conciseness: float
    output_tokens: int
    latency_ms: float
    feedback: str = ""

    def clamped(self) -> "TrialScore":
        return TrialScore(
            correctness=max(0.0, min(1.0, float(self.correctness))),
            procedure_following=max(0.0, min(1.0, float(self.procedure_following))),
            conciseness=max(0.0, min(1.0, float(self.conciseness))),
            output_tokens=max(0, int(self.output_tokens)),
            latency_ms=max(0.0, float(self.latency_ms)),
            feedback=str(self.feedback or "")[:4000],
        )


@dataclass
class CandidateMetrics:
    correctness: float = 0.0
    procedure_following: float = 0.0
    conciseness: float = 0.0
    avg_output_tokens: float = 0.0
    avg_latency_ms: float = 0.0
    prompt_tokens: int = 0
    composite: float = 0.0
    cases: int = 0
    failures: int = 0
    feedback: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


@dataclass
class EvolutionRun:
    id: str
    skill_name: str
    status: EvolutionStatus
    source: str
    baseline_sha256: str
    baseline_score: float = 0.0
    best_candidate_id: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    config: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def create(
        skill_name: str,
        source: str,
        baseline_sha256: str,
        config: dict[str, Any],
    ) -> "EvolutionRun":
        return EvolutionRun(
            id="evo_" + uuid.uuid4().hex[:16],
            skill_name=skill_name,
            status=EvolutionStatus.PREPARING,
            source=source,
            baseline_sha256=baseline_sha256,
            config=dict(config),
        )


@dataclass
class EvolutionCandidate:
    id: str
    run_id: str
    generation: int
    content: str
    content_sha256: str
    parent_id: str | None = None
    status: str = "CANDIDATE"
    score: float = 0.0
    metrics: CandidateMetrics = field(default_factory=CandidateMetrics)
    constraints: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def create(
        run_id: str,
        generation: int,
        content: str,
        parent_id: str | None = None,
    ) -> "EvolutionCandidate":
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return EvolutionCandidate(
            id="cand_" + uuid.uuid4().hex[:16],
            run_id=run_id,
            generation=int(generation),
            content=content,
            content_sha256=digest,
            parent_id=parent_id,
        )
