from __future__ import annotations

import json
import sys
from dataclasses import replace
from typing import Any

from kitt.core.runtime import KittRuntime
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    ApprovalRequired,
    MetricsRecorded,
    TextDelta,
    TurnBlocked,
    TurnCompleted,
    TurnFailed,
)
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.approval import ApprovalGrant


PREFIX = "KITT_CHILD_RESULT:"


def _emit(payload: dict[str, Any]) -> None:
    print(PREFIX + json.dumps(payload, ensure_ascii=False))


def _approval_grant(payload: dict) -> ApprovalGrant:
    return ApprovalGrant(
        approval_id=payload["approval_id"],
        turn_id=payload["turn_id"],
        conversation_id=payload["conversation_id"],
        workspace_id=payload["workspace_id"],
        action_hash=payload["action_hash"],
        granted_at=float(payload["granted_at"]),
        expires_at=float(payload["expires_at"]),
        nonce=payload["nonce"],
    )


def _validate_child_conversation(runtime: KittRuntime, conversation_id: str) -> None:
    conversation = runtime.history.repo.get_conversation(conversation_id)
    if not conversation or conversation.get("workspace_id") != runtime.workspace_id:
        raise PermissionError(
            "Retained child conversation is missing or outside workspace"
        )


def _bind_child_proxy_session(runtime: KittRuntime, request: dict) -> str:
    """Bind one retained child to one stable reverse-proxy browser session."""
    child_id = str(request.get("child_id") or "").strip()
    conversation_id = str(request.get("runtime_conversation_id") or "").strip()
    workspace_id = str(runtime.workspace_id or "").strip()
    if not child_id or not conversation_id or not workspace_id:
        raise ValueError("Child worker request is missing stable session identity")

    scope = f"retained-child:{workspace_id}:{child_id}"
    runtime.processor.set_proxy_session_scope(scope)
    return scope


def _turn_budget_usage(
    runtime: KittRuntime,
    turn_id: str,
    *,
    fallback_tokens: int = 0,
    fallback_cost: float = 0.0,
) -> dict[str, Any]:
    ledger = getattr(runtime.processor, "execution_budgets", {}).get(turn_id)
    if ledger is not None:
        try:
            usage = ledger.snapshot().get("usage", {})
            return {
                "tokens_used": max(0, int(usage.get("total_tokens", 0) or 0)),
                "calls_used": max(0, int(usage.get("model_calls", 0) or 0)),
                "cost_used": max(0.0, float(usage.get("cost", 0.0) or 0.0)),
                "tools_used": max(0, int(usage.get("tool_calls", 0) or 0)),
            }
        except Exception:
            pass
    return {
        "tokens_used": max(0, int(fallback_tokens)),
        "calls_used": 0,
        "cost_used": max(0.0, float(fallback_cost)),
        "tools_used": 0,
    }


def _run_new_turn(runtime: KittRuntime, request: dict) -> dict:
    child_conversation = request["runtime_conversation_id"]
    _validate_child_conversation(runtime, child_conversation)

    lease = request.get("budget_lease")
    if isinstance(lease, dict) and lease:
        consumed = (
            dict(lease.get("consumed") or {})
            if isinstance(lease.get("consumed"), dict)
            else {}
        )
        reserved = (
            dict(lease.get("reserved") or {})
            if isinstance(lease.get("reserved"), dict)
            else {}
        )
        token_cap = max(
            0,
            int(lease.get("token_cap", 0) or 0)
            - int(consumed.get("tokens", 0) or 0),
        )
        call_cap = max(
            0,
            int(lease.get("call_cap", 0) or 0)
            - int(consumed.get("calls", 0) or 0),
        )
        cost_cap = max(
            0.0,
            float(lease.get("cost_cap", 0.0) or 0.0)
            - float(consumed.get("cost", 0.0) or 0.0),
        )
        tool_cap = max(
            0,
            int(reserved.get("tools", 0) or 0)
            - int(consumed.get("tools", 0) or 0),
        )
        duration_ms = max(0, int(reserved.get("duration_ms", 0) or 0))
        if token_cap <= 0 or call_cap <= 0:
            raise RuntimeError("Child execution budget exhausted")
        current = runtime.processor.config
        runtime.processor.config = replace(
            current,
            max_model_calls_per_turn=min(
                int(getattr(current, "max_model_calls_per_turn", call_cap)),
                call_cap,
            ),
            max_input_tokens_per_turn=min(
                int(getattr(current, "max_input_tokens_per_turn", token_cap)),
                token_cap,
            ),
            max_output_tokens_per_turn=min(
                int(getattr(current, "max_output_tokens_per_turn", token_cap)),
                token_cap,
            ),
            max_total_tokens_per_turn=min(
                int(getattr(current, "max_total_tokens_per_turn", token_cap)),
                token_cap,
            ),
            max_cost_per_turn=min(
                float(getattr(current, "max_cost_per_turn", cost_cap)),
                cost_cap,
            ),
            max_tool_calls_per_turn=min(
                int(getattr(current, "max_tool_calls_per_turn", tool_cap)),
                tool_cap,
            ),
            max_turn_duration_seconds=min(
                float(
                    getattr(
                        current,
                        "max_turn_duration_seconds",
                        duration_ms / 1000.0 if duration_ms else 0.0,
                    )
                ),
                duration_ms / 1000.0,
            )
            if duration_ms > 0
            else float(getattr(current, "max_turn_duration_seconds", 1800.0)),
            max_subagents_per_turn=0,
        )

    source_context = ExecutionSecurityContext.from_dict(request["security_context"])
    security_context = ExecutionSecurityContext(
        workspace_id=source_context.workspace_id,
        conversation_id=child_conversation,
        turn_id="",
        origin="AGENT",
        principal_type="CHILD",
        principal_id=request["child_id"],
        capabilities=source_context.capabilities,
        trace_id=source_context.trace_id,
        parent_principal_id=source_context.parent_principal_id,
        path_scope=source_context.path_scope,
        fencing_token=source_context.fencing_token,
        fencing_owner_id=source_context.fencing_owner_id,
        fencing_subject_type=source_context.fencing_subject_type,
        fencing_subject_id=source_context.fencing_subject_id,
        fencing_subject_conversation_id=(
            source_context.fencing_subject_conversation_id
            or (
                source_context.conversation_id
                if source_context.fencing_subject_type == "GOAL"
                and source_context.fencing_subject_id
                else None
            )
        ),
    )
    command = TurnCommand(
        conversation_id=child_conversation,
        prompt=request["task"],
        mode="auto",
        explicit_files=set(request.get("allowed_paths", [])),
        security_context=security_context,
    )

    runtime.history.repo.save_message(
        child_conversation, command.turn_id, "user", request["task"]
    )
    chunks: list[str] = []
    final = ""
    tokens = 0
    cost = 0.0
    for event in runtime.processor.run_turn(command):
        if isinstance(event, TextDelta):
            chunks.append(event.delta)
        elif isinstance(event, MetricsRecorded):
            tokens += int(event.input_tokens) + int(event.output_tokens)
            cost += float(event.estimated_usd or 0.0)
        elif isinstance(event, TurnCompleted):
            final = event.response or "".join(chunks)
            if final:
                runtime.history.repo.save_message(
                    child_conversation, command.turn_id, "assistant", final
                )
        elif isinstance(event, ApprovalRequired):
            return {
                "success": False,
                "state": "WAITING_APPROVAL",
                "error": "Child operation requires parent/user approval",
                "approval_id": event.approval_request_id,
                "action_hash": event.action_hash,
                "turn_id": command.turn_id,
                **_turn_budget_usage(
                    runtime,
                    command.turn_id,
                    fallback_tokens=tokens,
                    fallback_cost=cost,
                ),
            }
        elif isinstance(event, (TurnFailed, TurnBlocked)):
            return {
                "success": False,
                "state": "FAILED",
                "error": getattr(
                    event, "error", getattr(event, "reason", "child failed")
                ),
                "turn_id": command.turn_id,
                **_turn_budget_usage(
                    runtime,
                    command.turn_id,
                    fallback_tokens=tokens,
                    fallback_cost=cost,
                ),
            }

    if not final and not chunks:
        return {
            "success": False,
            "state": "FAILED",
            "error": "Child turn ended without a completion event",
            "turn_id": command.turn_id,
            **_turn_budget_usage(
                runtime,
                command.turn_id,
                fallback_tokens=tokens,
                fallback_cost=cost,
            ),
        }
    return {
        "success": True,
        "state": "COMPLETED",
        "output": final or "".join(chunks),
        "turn_id": command.turn_id,
        **_turn_budget_usage(
            runtime,
            command.turn_id,
            fallback_tokens=tokens,
            fallback_cost=cost,
        ),
    }


def _continue_turn(runtime: KittRuntime, request: dict) -> dict:
    child_conversation = request["runtime_conversation_id"]
    _validate_child_conversation(runtime, child_conversation)
    grant = _approval_grant(request["grant"])
    turn_id = str(request["turn_id"])
    if grant.turn_id != turn_id or grant.conversation_id != child_conversation:
        raise PermissionError("Approval grant does not match retained child turn")

    final = ""
    for event in runtime.processor.continue_turn(turn_id, grant):
        if isinstance(event, TurnCompleted):
            final = event.response or ""
        elif isinstance(event, ApprovalRequired):
            return {
                "success": False,
                "state": "WAITING_APPROVAL",
                "error": "Child operation requires another approval",
                "approval_id": event.approval_request_id,
                "action_hash": event.action_hash,
                "turn_id": turn_id,
                "tokens_used": 0,
                "calls_used": 0,
                "cost_used": 0.0,
                "tools_used": 0,
            }
        elif isinstance(event, (TurnFailed, TurnBlocked)):
            return {
                "success": False,
                "state": "FAILED",
                "error": getattr(
                    event, "error", getattr(event, "reason", "child resume failed")
                ),
                "turn_id": turn_id,
                "tokens_used": 0,
                "calls_used": 0,
                "cost_used": 0.0,
                "tools_used": 0,
            }
    if not final:
        return {
            "success": False,
            "state": "FAILED",
            "error": "Child approval resume ended without completion",
            "turn_id": turn_id,
            "tokens_used": 0,
            "calls_used": 0,
            "cost_used": 0.0,
            "tools_used": 0,
        }
    return {
        "success": True,
        "state": "COMPLETED",
        "output": final,
        "turn_id": turn_id,
        "tokens_used": 0,
    }


def main() -> None:
    runtime = None
    try:
        request = json.loads(sys.stdin.read())
        runtime = KittRuntime.build(request["root"], state_root_dir=request.get("state_root"))
        _bind_child_proxy_session(runtime, request)
        mode = str(request.get("mode", "run"))
        if mode == "run":
            result = _run_new_turn(runtime, request)
        elif mode == "continue":
            result = _continue_turn(runtime, request)
        else:
            raise ValueError(f"Unknown child worker mode: {mode}")
        _emit(result)
    except Exception as exc:
        _emit({"success": False, "state": "FAILED", "error": str(exc)})
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    main()
