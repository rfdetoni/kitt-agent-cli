from __future__ import annotations

import json
import sys
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


def _run_new_turn(runtime: KittRuntime, request: dict) -> dict:
    child_conversation = request["runtime_conversation_id"]
    _validate_child_conversation(runtime, child_conversation)

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
    for event in runtime.processor.run_turn(command):
        if isinstance(event, TextDelta):
            chunks.append(event.delta)
        elif isinstance(event, MetricsRecorded):
            tokens += int(event.input_tokens) + int(event.output_tokens)
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
                "tokens_used": tokens,
            }
        elif isinstance(event, (TurnFailed, TurnBlocked)):
            return {
                "success": False,
                "state": "FAILED",
                "error": getattr(
                    event, "error", getattr(event, "reason", "child failed")
                ),
                "turn_id": command.turn_id,
                "tokens_used": tokens,
            }

    if not final and not chunks:
        return {
            "success": False,
            "state": "FAILED",
            "error": "Child turn ended without a completion event",
            "turn_id": command.turn_id,
            "tokens_used": tokens,
        }
    return {
        "success": True,
        "state": "COMPLETED",
        "output": final or "".join(chunks),
        "turn_id": command.turn_id,
        "tokens_used": tokens,
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
            }
    if not final:
        return {
            "success": False,
            "state": "FAILED",
            "error": "Child approval resume ended without completion",
            "turn_id": turn_id,
            "tokens_used": 0,
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
        runtime = KittRuntime.build(request["root"])
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
