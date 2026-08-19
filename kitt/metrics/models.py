import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

@dataclass
class TurnMetrics:
    turn_id: str
    conversation_id: str
    workspace_id: str = ""
    trace_id: Optional[str] = None
    goal_id: Optional[str] = None
    child_id: Optional[str] = None
    operation_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    context_model: str = ""
    execution_model: str = ""
    token_count_method: str = "estimated"
    candidate_tokens: int = 0
    naive_input_tokens: int = 0
    actual_input_tokens: int = 0
    actual_output_tokens: int = 0
    context_llm_input: int = 0
    context_llm_output: int = 0
    duration_ms: float = 0.0
    route: str = "dual-model"
    
    @property
    def gross_saved(self) -> int:
        return max(0, self.naive_input_tokens - self.actual_input_tokens)

    @property
    def context_cost(self) -> int:
        return self.context_llm_input + self.context_llm_output

    @property
    def net_saved(self) -> int:
        return self.gross_saved - self.context_cost

    @property
    def gross_saved_pct(self) -> float:
        return (self.gross_saved / max(1, self.naive_input_tokens)) * 100.0

    @property
    def net_saved_pct(self) -> float:
        return (self.net_saved / max(1, self.naive_input_tokens)) * 100.0
