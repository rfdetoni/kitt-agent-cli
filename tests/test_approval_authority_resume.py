from __future__ import annotations

import sys
import time
from dataclasses import replace

import pytest

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.core.pending_action import PendingAction
from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_events import TurnCompleted, TurnFailed
from kitt.security.authority_snapshot import capture_authority_snapshot, validate_authority_snapshot
from kitt.security.capabilities import CAP_PROCESS_RUN
from kitt.security.context import ExecutionSecurityContext


@pytest.fixture
def waiting_action(tmp_path):
    runtime = KittRuntime.build(
        str(tmp_path), RuntimeConfig(history_enabled=True, persistence_enabled=True)
    )
    try:
        conversation = runtime.history.new_conversation("approval resume")
        security = ExecutionSecurityContext(
            workspace_id=runtime.workspace_id,
            conversation_id=conversation["id"],
            turn_id="approval-turn",
            origin="MODEL",
            principal_type="GOAL",
            principal_id="goal-approval",
            capabilities=frozenset({CAP_PROCESS_RUN}),
            trace_id="approval-test",
        )
        with runtime.database.get_connection() as connection:
            connection.execute(
                "INSERT INTO turns(id,conversation_id,ordinal,state,mode,started_at) "
                "VALUES(?,?,1,'WAITING_APPROVAL','auto',?)",
                (security.turn_id, security.conversation_id, time.time()),
            )
        args = {
            "argv": [
                sys.executable,
                "-c",
                "with open('executed.txt', 'a') as output:\n    output.write('once\\n')",
            ],
            "cwd": ".",
            "network": False,
        }
        manager = runtime.approval
        # An existing rule protects the prefix/revocation invariant too.
        manager.remember("read_file", "**", "allow", "workspace", workspace_id=runtime.workspace_id)
        registry = runtime.registry
        snapshot_kwargs = {
            "policy": registry.policy,
            "autonomy": registry.policy.autonomy,
            "approval_manager": manager,
            "sandbox_profile": registry.process_runner.sandbox.default_profile,
        }
        envelope = capture_authority_snapshot(security, **snapshot_kwargs)
        action_hash = registry.policy.generate_action_hash("run_command", args)
        manager.register_request(
            security.turn_id,
            security.conversation_id,
            security.workspace_id,
            action_hash,
            "approval-request",
            tool_name="run_command",
        )
        action = PendingAction(
            id="pa_approval-turn",
            approval_request_id="approval-request",
            turn_id=security.turn_id,
            conversation_id=security.conversation_id,
            workspace_id=security.workspace_id,
            tool_name="run_command",
            normalized_args=args,
            action_hash=action_hash,
            source_response_sha256=runtime.processor._args_digest(args),
            affected_paths=[],
            before_hashes={},
            created_at=time.time(),
            expires_at=0.0,
            state="pending",
            security_context={**security.to_dict(), "authority_snapshot": envelope},
        )
        runtime.processor.pending_actions[security.turn_id] = action
        runtime.history.repo.save_pending_action(action)
        yield runtime, action, security, envelope, snapshot_kwargs
    finally:
        runtime.close()


def remember_command(runtime, security, scope="workspace", decision="allow"):
    runtime.approval.remember(
        "run_command",
        "**",
        decision,
        scope,
        conversation_id=security.conversation_id if scope == "session" else None,
        workspace_id=security.workspace_id,
        executable_identity="GOAL:goal-approval",
    )


def approve(runtime, action):
    return runtime.approval.issue_grant(
        action.turn_id,
        action.conversation_id,
        action.workspace_id,
        action.action_hash,
        approval_id=action.approval_request_id,
    )


@pytest.mark.parametrize("scope", ["once", "session", "workspace"])
def test_remember_then_approve_executes_exactly_once(waiting_action, tmp_path, scope):
    runtime, action, security, _, _ = waiting_action
    if scope != "once":
        remember_command(runtime, security, scope)
    # Restore persisted action, rather than relying on the hot in-memory copy.
    runtime.processor.pending_actions.clear()
    grant = approve(runtime, action)
    events = list(runtime.processor.continue_turn(action.turn_id, grant))
    assert any(isinstance(event, TurnCompleted) for event in events), events
    assert (tmp_path / "executed.txt").read_text() == "once\n"
    assert runtime.approval.is_nonce_used(grant.nonce)
    assert runtime.history.repo.get_valid_pending_action(action.id, action.workspace_id) is None
    replay = list(runtime.processor.continue_turn(action.turn_id, grant))
    assert any(isinstance(event, TurnFailed) for event in replay)
    assert (tmp_path / "executed.txt").read_text() == "once\n"


@pytest.mark.parametrize("change", ["deny", "revoke", "autonomy", "sandbox", "policy"])
def test_changed_authority_fails_and_retires_request(waiting_action, tmp_path, change):
    runtime, action, security, _, _ = waiting_action
    remember_command(runtime, security)
    if change == "deny":
        remember_command(runtime, security, decision="deny")
    elif change == "revoke":
        runtime.approval.clear_remembered(scope="workspace")
        remember_command(runtime, security)
    elif change == "autonomy":
        runtime.registry.policy.autonomy = AutonomyPolicy.preset("autonomous")
    elif change == "sandbox":
        runtime.registry.process_runner.sandbox.default_profile = "full-access"
    else:
        runtime.registry.policy.root_path = tmp_path / "changed"
    grant = approve(runtime, action)
    events = list(runtime.processor.continue_turn(action.turn_id, grant))
    assert any(
        isinstance(event, TurnFailed) and "authority changed" in event.error for event in events
    ), events
    assert not (tmp_path / "executed.txt").exists()
    assert action.turn_id not in runtime.processor.pending_actions
    assert runtime.history.repo.get_valid_pending_action(action.id, action.workspace_id) is None
    assert runtime.approval.list_pending(action.workspace_id) == []
    assert approve(runtime, action) is None


def test_other_snapshot_consumers_remain_strict(waiting_action):
    runtime, _, security, envelope, kwargs = waiting_action
    remember_command(runtime, security)
    remember_command(runtime, security, "session")
    with pytest.raises(PermissionError, match="approval_revision is stale"):
        validate_authority_snapshot(envelope, security, **kwargs)
    validate_authority_snapshot(envelope, security, allow_added_permissions=True, **kwargs)
    with pytest.raises(PermissionError, match="executable_identity mismatch"):
        validate_authority_snapshot(
            envelope,
            replace(security, principal_id="another-goal"),
            allow_added_permissions=True,
            **kwargs,
        )
