"""Persist one approval for the concrete tool selected by the host."""
from __future__ import annotations

import hashlib
import sqlite3
import time

from kitt.core.pending_action import PendingAction
from kitt.core.turn_events import ApprovalRequired, TurnBlocked
from kitt.security.authority_snapshot import capture_authority_snapshot
from kitt.security.mutation_preconditions import MutationPrecondition, capture_preconditions


def _concrete_action(metadata, tool_name, tool_args):
    action = str(metadata.get("approval_action") or tool_name)
    payload = metadata.get("approval_payload")
    if not isinstance(payload, dict):
        payload = tool_args
    resume_tool = str(metadata.get("resume_tool_name") or tool_name)
    resume_args = dict(payload) if metadata.get("resume_tool_name") else dict(tool_args)
    return action, payload, resume_tool, resume_args


def _preconditions(root_path, tool_name, args, metadata):
    preconditions = capture_preconditions(root_path, tool_name, args)
    if tool_name == "apply_patch" and "before_hashes" in metadata:
        hashes = metadata["before_hashes"]
        if not isinstance(hashes, dict):
            raise ValueError("Invalid patch precondition hashes")
        # Never re-baseline hashes taken before the approval was requested.
        return [
            MutationPrecondition(path, digest is not None, digest)
            for path, digest in hashes.items()
        ]
    return preconditions


def _pending_action(processor, cmd, workspace_id, tool_name, tool_args,
                    tool_result, security_context):
    metadata = tool_result.metadata or {}
    action, payload, resume_tool, resume_args = _concrete_action(
        metadata, tool_name, tool_args
    )
    action_hash = processor.registry.policy.generate_action_hash(action, payload)
    approval_id = f"req_{cmd.turn_id}_{hashlib.sha256(action_hash.encode()).hexdigest()[:8]}"
    preconditions = _preconditions(
        processor.root_path, resume_tool, resume_args, metadata
    )
    snapshot = security_context.to_dict()
    snapshot["mutation_preconditions"] = [p.to_dict() for p in preconditions]
    sandbox = getattr(
        getattr(processor.registry, "process_runner", None), "sandbox", None
    )
    snapshot["authority_snapshot"] = capture_authority_snapshot(
        security_context,
        policy=processor.registry.policy,
        autonomy=processor.registry.policy.autonomy,
        approval_manager=processor.registry.approval_manager,
        sandbox_profile=str(getattr(sandbox, "default_profile", "workspace-write")),
    )
    pending = PendingAction(
        id=f"pa_{cmd.turn_id}",
        approval_request_id=approval_id,
        turn_id=cmd.turn_id,
        conversation_id=cmd.conversation_id,
        workspace_id=workspace_id,
        tool_name=resume_tool,
        normalized_args=resume_args,
        action_hash=action_hash,
        source_response_sha256=processor._args_digest(resume_args),
        affected_paths=[p.path for p in preconditions],
        before_hashes={p.path: p.expected_sha256 for p in preconditions},
        created_at=time.time(),
        expires_at=0.0,
        state="pending",
        security_context=snapshot,
    )
    return pending, action, payload


def _retire_cancelled(processor, pending, history):
    processor.pending_actions.pop(pending.turn_id, None)
    if history is not None:
        try:
            history.repo.cancel_pending_action(pending.id)
        except Exception:
            pass
    processor.registry.approval_manager.deny(
        pending.approval_request_id, "Turn cancelled before approval"
    )


def require_tool_approval(processor, cmd, workspace_id, tool_name, tool_args,
                          tool_result, security_context):
    """Return an approval event, a typed block, or None after cancellation."""
    if not processor.turn_guard.begin(cmd.turn_id):
        return None
    pending = None
    history = processor.history_service
    saved = False
    try:
        pending, action, payload = _pending_action(
            processor, cmd, workspace_id, tool_name, tool_args,
            tool_result, security_context,
        )
        if history is not None:
            history.repo.save_pending_action(pending)
            saved = True
        processor.registry.approval_manager.register_request(
            cmd.turn_id, cmd.conversation_id, workspace_id,
            pending.action_hash, pending.approval_request_id, tool_name=action,
        )
        processor.pending_actions[cmd.turn_id] = pending
    except (ValueError, sqlite3.IntegrityError) as exc:
        if saved:
            history.repo.cancel_pending_action(pending.id)
        return TurnBlocked(reason=f"Approval registration rejected: {exc}")
    finally:
        processor.turn_guard.end(cmd.turn_id)

    if processor._cancel_requested(cmd.turn_id):
        _retire_cancelled(processor, pending, history)
        return None
    return ApprovalRequired(
        turn_id=cmd.turn_id,
        conversation_id=cmd.conversation_id,
        tool_name=action,
        args=payload,
        action_hash=pending.action_hash,
        approval_request_id=pending.approval_request_id,
        workspace_id=workspace_id,
        executable_identity=(
            f"{security_context.principal_type}:{security_context.principal_id}"
        ),
    )
