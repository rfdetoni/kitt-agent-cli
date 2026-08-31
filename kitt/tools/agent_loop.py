from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

from kitt.tools.policy_engine import PolicyEngine

LoopState = Literal["DISCOVER", "PLAN", "EDIT", "VERIFY", "REPAIR", "DONE", "BLOCKED"]


@dataclass
class LoopStepResult:
    step: int
    state: LoopState
    tool_name: str
    success: bool
    output: str
    error: Optional[str] = None


class AgentLoop:
    """Bounded stateful agent loop that always verifies successful repairs."""

    def __init__(self, max_steps: int = 10, max_same_failures: int = 2):
        self.max_steps = max(1, int(max_steps))
        self.max_same_failures = max(1, int(max_same_failures))
        self.state: LoopState = "DISCOVER"
        self.current_step = 0
        self.failure_history: List[str] = []
        self.history: List[LoopStepResult] = []
        self.policy = PolicyEngine()

    def can_continue(self) -> bool:
        if self.state in {"DONE", "BLOCKED"}:
            return False
        if self.current_step >= self.max_steps:
            self.state = "BLOCKED"
            return False
        return True

    def record_step(
        self,
        tool_name: str,
        success: bool,
        output: str,
        error: str | None = None,
    ) -> LoopState:
        self.current_step += 1
        prior_state = self.state
        self.history.append(
            LoopStepResult(
                step=self.current_step,
                state=prior_state,
                tool_name=tool_name,
                success=success,
                output=output,
                error=error,
            )
        )

        if not success:
            fail_key = f"{tool_name}:{error or 'unknown'}"
            self.failure_history.append(fail_key)
            if self.failure_history.count(fail_key) >= self.max_same_failures:
                self.state = "BLOCKED"
            else:
                self.state = "REPAIR"
            return self.state

        if prior_state in {"DISCOVER", "PLAN"}:
            self.state = "EDIT"
        elif prior_state == "EDIT":
            self.state = "VERIFY"
        elif prior_state == "REPAIR":
            # A successful repair is not proof that the original failure is
            # resolved; validation must run again before DONE.
            self.state = "VERIFY"
        elif prior_state == "VERIFY":
            self.state = "DONE"
        return self.state
