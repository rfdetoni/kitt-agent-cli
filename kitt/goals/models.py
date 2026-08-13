from dataclasses import dataclass,field
from typing import List,Optional
@dataclass(frozen=True)
class QualityGate:
    id: str; goal_id: str; name: str; argv: List[str]; status: str
    timeout_seconds: int = 120
    last_exit_code: Optional[int] = None; last_output_artifact_id: Optional[str] = None

@dataclass(frozen=True)
class Goal:
    id: str; conversation_id: str; objective: str; state: str
    token_budget: Optional[int]; max_turns: int; max_wall_seconds: int
    tokens_used: int; turns_used: int; continuations_used: int
    success_criteria: List[str] = field(default_factory=list)
    started_at: float = 0; updated_at: float = 0; completed_at: Optional[float] = None
    last_error: Optional[str] = None
    gates: List[QualityGate] = field(default_factory=list)
