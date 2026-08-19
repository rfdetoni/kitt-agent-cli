from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class QualityGate:
    id: str
    goal_id: str
    name: str
    argv: List[str]
    status: str
    timeout_seconds: int = 120
    last_exit_code: Optional[int] = None
    last_output_artifact_id: Optional[str] = None


@dataclass(frozen=True)
class Goal:
    id: str
    conversation_id: str
    objective: str
    state: str
    token_budget: Optional[int]
    max_turns: int
    max_wall_seconds: int
    tokens_used: int
    turns_used: int
    continuations_used: int
    success_criteria: List[str] = field(default_factory=list)
    started_at: float = 0
    updated_at: float = 0
    completed_at: Optional[float] = None
    last_error: Optional[str] = None
    gates: List[QualityGate] = field(default_factory=list)
    scheduled_at: Optional[float] = None
    next_run_at: Optional[float] = None
    recurrence: Optional[str] = None
    heartbeat_enabled: bool = False
    resume_policy: str = "manual"
    owner_session_id: Optional[str] = None
    lease_id: Optional[str] = None
    lease_expires_at: Optional[float] = None
    lease_owner_id: Optional[str] = None
    lease_heartbeat_at: Optional[float] = None
    max_cost: float = 0.0
    cost_used: float = 0.0
    max_failures: int = 5
    max_retries: int = 3
    max_children: int = 5
    failures_used: int = 0
    retries_used: int = 0
    children_used: int = 0
    capabilities: List[str] = field(default_factory=list)
