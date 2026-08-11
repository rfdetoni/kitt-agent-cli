import time
from typing import List, Dict, Any, Optional, Tuple, Literal
from dataclasses import dataclass, field
from kitt.tools.policy_engine import PolicyEngine

LoopState = Literal['DISCOVER', 'PLAN', 'EDIT', 'VERIFY', 'REPAIR', 'DONE', 'BLOCKED']

@dataclass
class LoopStepResult:
    step: int
    state: LoopState
    tool_name: str
    success: bool
    output: str
    error: Optional[str] = None

class AgentLoop:
    """Bounded, stateful agent execution loop for small models."""

    def __init__(self, max_steps: int = 10, max_same_failures: int = 2):
        self.max_steps = max_steps
        self.max_same_failures = max_same_failures
        self.state: LoopState = 'DISCOVER'
        self.current_step = 0
        self.failure_history: List[str] = []
        self.history: List[LoopStepResult] = []
        self.policy = PolicyEngine()

    def can_continue(self) -> bool:
        if self.state in {'DONE', 'BLOCKED'}:
            return False
        if self.current_step >= self.max_steps:
            self.state = 'BLOCKED'
            return False
        return True

    def record_step(self, tool_name: str, success: bool, output: str, error: str = None) -> LoopState:
        self.current_step += 1
        res = LoopStepResult(
            step=self.current_step,
            state=self.state,
            tool_name=tool_name,
            success=success,
            output=output,
            error=error
        )
        self.history.append(res)

        if not success:
            fail_key = f"{tool_name}:{error or 'unknown'}"
            self.failure_history.append(fail_key)
            same_count = self.failure_history.count(fail_key)

            if same_count >= self.max_same_failures:
                self.state = 'BLOCKED'
            else:
                self.state = 'REPAIR'
        else:
            if self.state in {'DISCOVER', 'PLAN'}:
                self.state = 'EDIT'
            elif self.state == 'EDIT':
                self.state = 'VERIFY'
            elif self.state in {'VERIFY', 'REPAIR'}:
                self.state = 'DONE'

        return self.state
