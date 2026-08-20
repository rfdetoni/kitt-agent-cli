from __future__ import annotations

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    ApprovalRequired,
    MetricsRecorded,
    TurnBlocked,
    TurnCompleted,
    TurnFailed,
)
from kitt.runtime.state import RuntimeStateStore
from kitt.security.context import ExecutionSecurityContext


class GoalStepExecutor:
    """Execute scheduler work through the canonical TurnProcessor policy path."""

    RESUME_KEY_PREFIX = "goal.resume:"

    def __init__(self, runtime_getter):
        self.runtime_getter = runtime_getter

    @classmethod
    def _resume_key(cls, goal_id: str) -> str:
        return f"{cls.RESUME_KEY_PREFIX}{goal_id}"

    def __call__(self, goal, *, lease_id=None, lease_owner_id=None):
        runtime = self.runtime_getter()
        state = RuntimeStateStore(
            runtime.database,
            runtime.workspace_id,
            goal.conversation_id,
        )
        resume = state.get(self._resume_key(goal.id))
        prompt = goal.objective
        if isinstance(resume, dict):
            approved_output = str(resume.get("tool_output") or "")[:32768]
            prompt = (
                "Continue the existing persistent goal after an approved host "
                "action. The approved action already succeeded; do not repeat "
                "it. Use the existing conversation/history and complete only "
                "the remaining work.\n\n"
                f"Approved host result:\n{approved_output}\n\n"
                f"Original objective:\n{goal.objective}"
            )

        security = ExecutionSecurityContext(
            workspace_id=runtime.workspace_id,
            conversation_id=goal.conversation_id,
            turn_id="",
            origin="SCHEDULE",
            principal_type="GOAL",
            principal_id=goal.id,
            capabilities=frozenset(goal.capabilities),
            trace_id=f"goal:{goal.id}",
            fencing_token=lease_id,
            fencing_owner_id=lease_owner_id,
            fencing_subject_type="GOAL",
            fencing_subject_id=goal.id,
        )
        command = TurnCommand(
            conversation_id=goal.conversation_id,
            prompt=prompt,
            mode="auto",
            security_context=security,
        )
        result = {
            "status": "FAILED",
            "tokens": 0,
            "cost": 0.0,
            "turn_id": command.turn_id,
            "response": "",
            "resumed": bool(resume),
        }
        for event in runtime.processor.run_turn(command):
            if isinstance(event, MetricsRecorded):
                result["tokens"] += event.input_tokens + event.output_tokens
                result["cost"] += float(event.estimated_usd or 0.0)
            elif isinstance(event, ApprovalRequired):
                result.update(
                    status="WAITING_APPROVAL",
                    approval_id=event.approval_request_id,
                )
                return result
            elif isinstance(event, TurnCompleted):
                result.update(status="SUCCEEDED", response=event.response)
            elif isinstance(event, TurnBlocked):
                result.update(status="BLOCKED", error=event.reason)
            elif isinstance(event, TurnFailed):
                result.update(status="FAILED", error=event.error)

        if result["status"] == "SUCCEEDED" and resume is not None:
            state.delete(self._resume_key(goal.id))
        return result
