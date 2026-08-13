"""Progressive escalation manager and handoff compact builder."""

from __future__ import annotations

from typing import List, Dict, Any, Tuple, Optional
from kitt.router.models import ExecutionHandoff

ESCALATION_STATES = {
    "SMALL_ATTEMPT",
    "RETRY_SMALL",
    "ESCALATE_LARGE",
    "SUCCESS",
    "HUMAN_APPROVAL",
    "BLOCKED"
}


class EscalationManager:
    """Manages progressive escalation state machine between small and large model execution."""

    def __init__(self, max_small_retries: int = 1):
        self.max_small_retries = max_small_retries
        self.state = "SMALL_ATTEMPT"
        self.retry_count = 0
        self.errors: List[str] = []
        self.verified_facts: List[str] = []
        self.tool_results: List[Dict[str, Any]] = []
        self.validation_results: List[Dict[str, Any]] = []
        self.tokens_in = 0
        self.tokens_out = 0

    def record_attempt(self, success: bool, error: Optional[str] = None, tokens_in: int = 0, tokens_out: int = 0) -> str:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out

        if success:
            self.state = "SUCCESS"
            return self.state

        if error:
            self.errors.append(error)

        if self.state == "SMALL_ATTEMPT":
            if self.retry_count < self.max_small_retries:
                self.retry_count += 1
                self.state = "RETRY_SMALL"
            else:
                self.state = "ESCALATE_LARGE"
        elif self.state == "RETRY_SMALL":
            self.state = "ESCALATE_LARGE"
        elif self.state == "ESCALATE_LARGE":
            self.state = "BLOCKED"

        return self.state

    def add_fact(self, fact: str) -> None:
        if fact and fact not in self.verified_facts:
            self.verified_facts.append(fact)

    def add_tool_result(self, tool_name: str, success: bool, summary: str) -> None:
        self.tool_results.append({
            "tool": tool_name,
            "success": success,
            "summary": summary[:200]
        })

    def create_handoff(self, original_task: str, sources: List[Dict[str, Any]] | None = None) -> ExecutionHandoff:
        """Build a compact ExecutionHandoff payload containing facts and results without chain-of-thought."""
        return ExecutionHandoff(
            original_task=original_task,
            verified_facts=tuple(self.verified_facts),
            selected_sources=tuple(sources or []),
            tool_results=tuple(self.tool_results),
            errors=tuple(self.errors),
            validation_results=tuple(self.validation_results),
            pending_actions=(),
            input_tokens_spent=self.tokens_in,
            output_tokens_spent=self.tokens_out
        )
