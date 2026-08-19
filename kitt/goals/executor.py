from __future__ import annotations

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    ApprovalRequired, MetricsRecorded, TurnBlocked, TurnCompleted, TurnFailed,
)
from kitt.security.context import ExecutionSecurityContext


class GoalStepExecutor:
    """Execute scheduler work through the same TurnProcessor/SafeRuntime policy path."""

    def __init__(self, runtime_getter):
        self.runtime_getter = runtime_getter

    def __call__(self, goal):
        runtime = self.runtime_getter()
        sec = ExecutionSecurityContext(
            workspace_id=runtime.workspace_id,
            conversation_id=goal.conversation_id,
            turn_id="",
            origin="SCHEDULE",
            principal_type="GOAL",
            principal_id=goal.id,
            capabilities=frozenset(goal.capabilities),
            trace_id=f"goal:{goal.id}",
        )
        cmd = TurnCommand(
            conversation_id=goal.conversation_id,
            prompt=goal.objective,
            mode="auto",
            security_context=sec,
        )
        result = {
            "status": "FAILED", "tokens": 0, "cost": 0.0,
            "turn_id": cmd.turn_id, "response": "",
        }
        for event in runtime.processor.run_turn(cmd):
            if isinstance(event, MetricsRecorded):
                result["tokens"] += event.input_tokens + event.output_tokens
                result["cost"] += float(event.estimated_usd or 0.0)
            elif isinstance(event, ApprovalRequired):
                result.update(status="WAITING_APPROVAL", approval_id=event.approval_request_id)
                return result
            elif isinstance(event, TurnCompleted):
                result.update(status="SUCCEEDED", response=event.response)
            elif isinstance(event, TurnBlocked):
                result.update(status="BLOCKED", error=event.reason)
            elif isinstance(event, TurnFailed):
                result.update(status="FAILED", error=event.error)
        return result
