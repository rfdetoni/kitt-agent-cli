from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class WorkingState:
    """Structured long-session state retained across compaction boundaries."""

    objective: str = ""
    current_state: tuple[str, ...] = ()
    constraints_and_decisions: tuple[str, ...] = ()
    affected_artifacts: tuple[str, ...] = ()
    errors_and_corrections: tuple[str, ...] = ()
    pending_work: tuple[str, ...] = ()
    validation_state: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "objective": self.objective,
            "current_state": list(self.current_state),
            "constraints_and_decisions": list(self.constraints_and_decisions),
            "affected_artifacts": list(self.affected_artifacts),
            "errors_and_corrections": list(self.errors_and_corrections),
            "pending_work": list(self.pending_work),
            "validation_state": list(self.validation_state),
        }

    def render(self) -> str:
        sections = [
            ("Objective", (self.objective,) if self.objective else ()),
            ("Current State", self.current_state),
            ("Constraints & Decisions", self.constraints_and_decisions),
            ("Affected Artifacts", self.affected_artifacts),
            ("Errors & Corrections", self.errors_and_corrections),
            ("Pending Work", self.pending_work),
            ("Validation", self.validation_state),
        ]
        out: list[str] = []
        for title, values in sections:
            if not values:
                continue
            out.append(f"## {title}")
            out.extend(f"- {value}" for value in values)
        return "\n".join(out)


@dataclass(frozen=True)
class CompactionResult:
    id: str
    conversation_id: str
    entry_id: str
    summary: str
    tokens_before: int
    tokens_after: int
    valid: bool
    validation: Dict[str, object] = field(default_factory=dict)
    working_state: WorkingState = field(default_factory=WorkingState)
    checkpoint: Dict[str, object] = field(default_factory=dict)
    recovery_refs: tuple[Dict[str, object], ...] = ()
