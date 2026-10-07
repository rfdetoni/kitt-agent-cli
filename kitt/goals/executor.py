from __future__ import annotations

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    ApprovalRequired,
    EditApplied,
    MetricsRecorded,
    ToolCompleted,
    ToolStarted,
    TurnBlocked,
    TurnCompleted,
    TurnFailed,
)
from kitt.goals.completion import AutonomousCompletionEngine
from kitt.goals.auto_contract import goal_active_turn_key
from kitt.goals.contract_execution import build_contract_prompt, prepare_contract_step
from kitt.goals.gates import QualityGateRunner
from kitt.runtime.state import RuntimeStateStore
from kitt.goals.review_snapshot import paths_from_tool_start
from kitt.goals.step_verifier import GoalStepVerifier
from kitt.security.context import ExecutionSecurityContext


class GoalStepExecutor:
    """Execute scheduler work through the canonical TurnProcessor policy path."""

    RESUME_KEY_PREFIX = "goal.resume:"

    @staticmethod
    def _blocked_category(reason: str) -> str:
        """Classify host-owned block reasons without asking the model to authorize stopping."""
        value = str(reason or "")
        if value.startswith("No progress:") or value.startswith(
            "Host evidence does not satisfy"
        ):
            return "RESOLVABLE"
        if value.startswith("Unresolved prior execution receipt:"):
            return "ENVIRONMENT"
        lowered = value.casefold()
        if "execution denied by policyengine" in lowered or "capability is not granted" in lowered:
            return "POLICY"
        return "UNSPECIFIED"

    def __init__(self, runtime_getter, reviewer_factory=None):
        self.runtime_getter = runtime_getter
        self.reviewer_factory = reviewer_factory

    @classmethod
    def _resume_key(cls, goal_id: str) -> str:
        return f"{cls.RESUME_KEY_PREFIX}{goal_id}"

    @staticmethod
    def _completion_engine(runtime, goal) -> AutonomousCompletionEngine:
        def authorize_gate(argv):
            return runtime.policy.evaluate_tool(
                "run_command",
                {"argv": list(argv)},
                origin="SCHEDULE",
                conversation_id=goal.conversation_id,
                workspace_id=runtime.workspace_id,
            )

        return AutonomousCompletionEngine(
            gate_runner=QualityGateRunner(runtime.registry.process_runner),
            gate_result_recorder=runtime.goals.record_gate_result,
            gate_authorizer=authorize_gate,
        )

    def __call__(self, goal, *, lease_id=None, lease_owner_id=None):
        runtime = self.runtime_getter()
        verifier = GoalStepVerifier(self.reviewer_factory)
        state = RuntimeStateStore(
            runtime.database,
            runtime.workspace_id,
            goal.conversation_id,
        )
        resume = state.get(self._resume_key(goal.id))
        contract_step = prepare_contract_step(runtime, goal, state)
        contract_item = contract_step.item
        step_goal = contract_step.goal
        completion_key = contract_step.completion_key
        completion_state = contract_step.completion_state
        completion = self._completion_engine(runtime, step_goal)

        prompt = build_contract_prompt(runtime, goal, contract_step, resume)
        prompt = completion.build_execution_prompt(step_goal, prompt, completion_state)

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
        prior_review_paths = list((completion_state or {}).get("review_paths") or [])
        if isinstance(resume, dict):
            prior_review_paths.extend(list(resume.get("affected_paths") or []))
        current_review_paths = []
        pending_mutation_paths = {}
        active_turn_key = goal_active_turn_key(goal.id)
        state.set(
            active_turn_key,
            {"turn_id": command.turn_id},
            ttl_seconds=24 * 60 * 60,
        )
        try:
            for event in runtime.processor.run_turn(command):
                if isinstance(event, ToolStarted):
                    paths = paths_from_tool_start(runtime, event)
                    if paths:
                        pending_mutation_paths[event.call_id] = paths
                elif isinstance(event, ToolCompleted):
                    paths = pending_mutation_paths.pop(event.call_id, [])
                    if event.success and paths:
                        current_review_paths.extend(paths)
                elif isinstance(event, EditApplied):
                    current_review_paths.extend(list(event.applied_files) + list(event.created_files))
                elif isinstance(event, MetricsRecorded):
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
                    edit_result = getattr(event, "edit_result", None)
                    if edit_result is not None and getattr(edit_result, "success", False):
                        current_review_paths.extend(
                            list(getattr(edit_result, "applied_files", None) or [])
                            + list(getattr(edit_result, "created_files", None) or [])
                        )
                elif isinstance(event, TurnBlocked):
                    category = self._blocked_category(event.reason)
                    result.update(
                        status="INCOMPLETE" if category == "RESOLVABLE" else "BLOCKED",
                        error=event.reason,
                        block_reason=category,
                    )
                elif isinstance(event, TurnFailed):
                    result.update(
                        status="INCOMPLETE" if event.recoverable else "FAILED",
                        error=event.error,
                        block_reason="ENVIRONMENT" if event.recoverable else "",
                    )
        finally:
            state.delete(active_turn_key)

        if result["status"] == "SUCCEEDED":
            return verifier.finalize(
                runtime=runtime,
                goal=goal,
                contract_item=contract_item,
                step_goal=step_goal,
                completion=completion,
                completion_state=completion_state,
                state=state,
                completion_key=completion_key,
                resume=resume,
                resume_key=self._resume_key(goal.id),
                result=result,
                security=security,
                turn_id=command.turn_id,
                prior_review_paths=prior_review_paths,
                current_review_paths=current_review_paths,
            )
        return result
