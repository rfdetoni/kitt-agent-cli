"""Regression admission gate for KITT agent-engine changes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class AgentScorecard:
    quality: float
    efficiency: float
    reliability: float
    autonomy: float

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "AgentScorecard":
        return cls(*(float(data[name]) for name in ("quality", "efficiency", "reliability", "autonomy")))

    def __post_init__(self):
        for name in ("quality", "efficiency", "reliability", "autonomy"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in 0..1")


@dataclass(frozen=True)
class AdmissionResult:
    accepted: bool
    deltas: dict[str, float] = field(default_factory=dict)
    regressions: tuple[str, ...] = ()


class AgentAdmissionGate:
    """Reject changes that materially regress any core agent dimension.

    A candidate may trade tiny noise within ``max_regression`` but must improve
    the weighted total by ``min_total_gain`` unless it is effectively neutral.
    This makes architectural evolution empirical rather than feature-driven.
    """

    DIMENSIONS = ("quality", "efficiency", "reliability", "autonomy")

    def __init__(self, max_regression: float = 0.02, min_total_gain: float = 0.0):
        self.max_regression = max(0.0, float(max_regression))
        self.min_total_gain = float(min_total_gain)

    def evaluate(self, baseline: AgentScorecard, candidate: AgentScorecard) -> AdmissionResult:
        deltas = {name: getattr(candidate, name) - getattr(baseline, name) for name in self.DIMENSIONS}
        regressions = tuple(name for name, delta in deltas.items() if delta < -self.max_regression)
        total_gain = sum(deltas.values()) / len(deltas)
        accepted = not regressions and total_gain >= self.min_total_gain
        return AdmissionResult(accepted=accepted, deltas=deltas, regressions=regressions)
