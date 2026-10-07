from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    ApprovalRequired,
    EditApplied,
    TextDelta,
    TurnBlocked,
    TurnCancelled,
    TurnCompleted,
    TurnEvent,
    TurnFailed,
    TurnStarted,
)
from kitt.goals.contract import ContractPlanner
from kitt.goals.contract_commands import render_contract
from kitt.runtime.state import RuntimeStateStore
from kitt.security.capabilities import (
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
    CAP_PROCESS_RUN,
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_REPO_WRITE,
)
from kitt.security.context import ExecutionSecurityContext


_AUTO_CONTRACT_TTL_SECONDS = 24 * 60 * 60
_TERMINAL_GOAL_STATES = {
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "PAUSED_BUDGET_EXCEEDED",
}
_AUTOMATIC_CODING_CAPABILITIES = (
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
    CAP_PROCESS_RUN,
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_REPO_WRITE,
)


def _outer_state(runtime, conversation_id: str) -> RuntimeStateStore:
    return RuntimeStateStore(
        runtime.database,
        runtime.workspace_id,
        conversation_id,
    )


def _outer_state_key(turn_id: str) -> str:
    return f"auto-contract:{turn_id}"


def goal_resume_key(goal_id: str) -> str:
    return f"goal.resume:{goal_id}"


def automatic_contract_capabilities() -> list[str]:
    """Host-owned capability ceiling for normal autonomous coding requests."""
    return sorted(_AUTOMATIC_CODING_CAPABILITIES)


def _pending_action_for_goal(runtime, goal_id: str, conversation_id: str):
    pending = runtime.approval.list_pending(
        workspace_id=runtime.workspace_id,
        conversation_id=conversation_id,
    )
    for request in pending:
        action = runtime.history.repo.get_valid_pending_action(
            f"pa_{request.turn_id}",
            runtime.workspace_id,
        )
        if action is None or not action.security_context:
            continue
        try:
            security = ExecutionSecurityContext.from_dict(action.security_context)
        except Exception:
            continue
        if (
            security.principal_type == "GOAL"
            and security.principal_id == goal_id
            and action.conversation_id == conversation_id
        ):
            return request, action, security
    return None


def _approval_event(runtime, goal_id: str, conversation_id: str):
    found = _pending_action_for_goal(runtime, goal_id, conversation_id)
    if found is None:
        return None
    request, action, security = found
    return ApprovalRequired(
        turn_id=request.turn_id,
        conversation_id=request.conversation_id,
        tool_name=action.tool_name,
        args=dict(action.normalized_args),
        action_hash=request.normalized_args_hash,
        approval_request_id=request.approval_id,
        workspace_id=request.workspace_id,
        executable_identity=(
            f"{security.principal_type}:{security.principal_id}"
        ),
    )


def _progress_signature(goal, items) -> tuple:
    return (
        goal.state,
        tuple(
            (item.local_id, item.status, item.attempts, item.last_feedback or "")
            for item in items
        ),
    )


def _progress_text(goal, items) -> str:
    current = next((item for item in items if item.status != "DONE"), None)
    if goal.state == "SUCCEEDED":
        return f"\n[contract] {goal.id}: 100% concluído.\n"
    if current is None:
        return f"\n[contract] {goal.id}: finalizando validação.\n"
    return (
        f"\n[contract] {current.local_id} {current.status.lower()} "
        f"(tentativa {current.attempts}/{current.max_attempts}) — "
        f"{current.title}\n"
    )


def automatic_contract_goal_id(
    runtime,
    conversation_id: str,
    outer_turn_id: str,
) -> str:
    value = _outer_state(runtime, conversation_id).get(
        _outer_state_key(outer_turn_id)
    )
    if not isinstance(value, dict):
        return ""
    return str(value.get("goal_id") or "")


def cancel_automatic_contract(
    runtime,
    conversation_id: str,
    outer_turn_id: str,
    reason: str,
) -> bool:
    goal_id = automatic_contract_goal_id(
        runtime,
        conversation_id,
        outer_turn_id,
    )
    if not goal_id:
        return False
    goal = runtime.goals.get_scoped(goal_id, conversation_id)
    if goal is None or goal.state in _TERMINAL_GOAL_STATES:
        return False
    runtime.goals.update_state(
        goal_id,
        "CANCELLED",
        reason,
        conversation_id=conversation_id,
    )
    return True


def iter_automatic_contract(runtime, command: TurnCommand) -> Iterator[TurnEvent]:
    """Execute a normal user auto request through the durable Goal contract."""
    yield TurnStarted(
        turn_id=command.turn_id,
        conversation_id=command.conversation_id,
        prompt=command.prompt,
    )
    if command.no_history:
        yield TurnFailed(
            error=(
                "Automatic contract execution requires persistent state; "
                "no_history is incompatible with mode=auto."
            ),
            turn_id=command.turn_id,
            conversation_id=command.conversation_id,
        )
        return

    try:
        items = ContractPlanner(runtime).plan(
            command.conversation_id,
            command.prompt,
        )
        goal = runtime.goals.create_contract(
            command.conversation_id,
            command.prompt,
            items,
            automatic_contract_capabilities(),
            None,
            1800,
            5,
            start_paused=False,
        )
        if not runtime.goal_scheduler.schedule_goal(
            goal.id,
            heartbeat_enabled=True,
            resume_policy="auto",
            owner_session_id=command.conversation_id,
        ):
            raise RuntimeError(f"Unable to schedule automatic contract {goal.id}")
    except Exception as exc:
        yield TurnFailed(
            error=f"Automatic contract planning failed: {exc}",
            turn_id=command.turn_id,
            conversation_id=command.conversation_id,
        )
        return

    state = _outer_state(runtime, command.conversation_id)
    state.set(
        _outer_state_key(command.turn_id),
        {"goal_id": goal.id},
        ttl_seconds=_AUTO_CONTRACT_TTL_SECONDS,
    )
    yield TextDelta(
        delta=(
            f"[contract] {goal.id}: plano criado com {len(items)} itens; "
            "iniciando execução sequencial.\n"
        )
    )

    last_signature = None
    emitted_approvals: set[str] = set()
    deadline = time.monotonic() + max(60.0, float(goal.max_wall_seconds) + 30.0)

    try:
        while True:
            current_goal = runtime.goals.get(goal.id)
            if current_goal is None:
                yield TurnFailed(
                    error=f"Automatic contract disappeared: {goal.id}",
                    turn_id=command.turn_id,
                    conversation_id=command.conversation_id,
                )
                return
            contract_items = runtime.goals.contract_items(goal.id)
            signature = _progress_signature(current_goal, contract_items)
            if signature != last_signature:
                yield TextDelta(delta=_progress_text(current_goal, contract_items))
                last_signature = signature

            if current_goal.state == "WAITING_APPROVAL":
                approval = _approval_event(
                    runtime,
                    goal.id,
                    command.conversation_id,
                )
                if (
                    approval is not None
                    and approval.approval_request_id not in emitted_approvals
                ):
                    emitted_approvals.add(approval.approval_request_id)
                    yield approval

            if current_goal.state == "SUCCEEDED":
                yield TurnCompleted(response=render_contract(runtime, goal.id))
                return
            if current_goal.state in _TERMINAL_GOAL_STATES:
                yield TurnFailed(
                    error=(
                        current_goal.last_error
                        or f"Automatic contract ended in {current_goal.state}"
                    ),
                    turn_id=command.turn_id,
                    conversation_id=command.conversation_id,
                )
                return
            if time.monotonic() >= deadline:
                yield TurnFailed(
                    error=f"Automatic contract exceeded its wall-clock observation window: {goal.id}",
                    turn_id=command.turn_id,
                    conversation_id=command.conversation_id,
                    recoverable=True,
                    recovery_action="continue",
                )
                return
            time.sleep(0.2)
    finally:
        state.delete(_outer_state_key(command.turn_id))


@dataclass(frozen=True)
class GoalApprovalResolution:
    handled: bool
    goal_id: str = ""
    success: bool = False
    error: str = ""


def goal_id_for_pending_turn(runtime, turn_id: str) -> str:
    action = runtime.history.repo.get_valid_pending_action(
        f"pa_{turn_id}",
        runtime.workspace_id,
    )
    if action is None or not action.security_context:
        return ""
    try:
        security = ExecutionSecurityContext.from_dict(action.security_context)
    except Exception:
        return ""
    if security.principal_type != "GOAL":
        return ""
    return str(security.principal_id or "")


def resolve_goal_approval(runtime, grant) -> GoalApprovalResolution:
    """Consume an approved GOAL tool internally, then resume the same item."""
    goal_id = goal_id_for_pending_turn(runtime, grant.turn_id)
    if not goal_id:
        return GoalApprovalResolution(False)

    response = ""
    affected_paths: list[str] = []
    error = ""
    success = False
    for event in runtime.processor.continue_turn(grant.turn_id, grant):
        if isinstance(event, EditApplied):
            affected_paths.extend(
                [*event.applied_files, *event.created_files]
            )
        elif isinstance(event, TurnCompleted):
            response = event.response
            edit_result = getattr(event, "edit_result", None)
            if edit_result is not None and getattr(edit_result, "success", False):
                affected_paths.extend(
                    list(getattr(edit_result, "applied_files", None) or [])
                    + list(getattr(edit_result, "created_files", None) or [])
                )
            success = True
        elif isinstance(event, TurnBlocked):
            error = event.reason
        elif isinstance(event, TurnFailed):
            error = event.error
        elif isinstance(event, TurnCancelled):
            error = event.reason

    if not success:
        runtime.goals.block_waiting_contract(
            goal_id,
            error or "Approved contract action did not complete successfully.",
            conversation_id=grant.conversation_id,
        )
        return GoalApprovalResolution(
            True,
            goal_id,
            False,
            error or "Approved contract action failed.",
        )

    state = _outer_state(runtime, grant.conversation_id)
    state.set(
        goal_resume_key(goal_id),
        {
            "tool_output": response,
            "affected_paths": list(dict.fromkeys(affected_paths)),
        },
        ttl_seconds=_AUTO_CONTRACT_TTL_SECONDS,
    )
    resumed = runtime.goals.resume_after_approval(
        goal_id,
        conversation_id=grant.conversation_id,
    )
    if resumed is None:
        return GoalApprovalResolution(
            True,
            goal_id,
            False,
            "Goal could not be resumed after approved action.",
        )
    return GoalApprovalResolution(True, goal_id, True)


def deny_goal_approval(
    runtime,
    *,
    turn_id: str,
    approval_id: str,
    conversation_id: str,
    reason: str,
) -> GoalApprovalResolution:
    goal_id = goal_id_for_pending_turn(runtime, turn_id)
    if not goal_id:
        return GoalApprovalResolution(False)
    runtime.approval.deny(approval_id, reason)
    runtime.history.repo.cancel_pending_action(f"pa_{turn_id}")
    runtime.goals.block_waiting_contract(
        goal_id,
        reason,
        conversation_id=conversation_id,
    )
    return GoalApprovalResolution(True, goal_id, False, reason)
