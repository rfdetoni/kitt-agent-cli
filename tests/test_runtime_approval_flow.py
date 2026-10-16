from __future__ import annotations

import json
import io
import time
from dataclasses import replace
from unittest.mock import patch

import pytest

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.core.execution_request import ExecutionRequest
from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import ApprovalRequired, TurnCompleted, TurnFailed
from kitt.domain.entities import ModelProfile
from kitt.goals.auto_contract import resolve_goal_approval
from kitt.security.capabilities import CAP_PROCESS_RUN, CAP_REPO_WRITE
from kitt.security.context import ExecutionSecurityContext
from kitt.ui.fallback import PlainLineUI


@pytest.fixture
def model_goal(tmp_path):
    runtime = KittRuntime.build(
        str(tmp_path), RuntimeConfig(history_enabled=True, persistence_enabled=True)
    )
    try:
        conversation_id = runtime.history.new_conversation("model approval flow")["id"]
        items = [
            dict(
                local_id=local_id,
                kind=kind,
                title=local_id,
                prompt="inspect",
                validation_prompt="verify",
                success_criteria=["verified"],
                check_ids=[],
                paths=[],
                depends_on=dependencies,
            )
            for local_id, kind, dependencies in [("T01", "task", []), ("FINAL", "final", ["T01"])]
        ]
        goal = runtime.goals.create_contract(
            conversation_id, "approval flow", items, [CAP_PROCESS_RUN], None, 1800, 5
        )
        security = ExecutionSecurityContext(
            workspace_id=runtime.workspace_id,
            conversation_id=conversation_id,
            turn_id="model-turn",
            origin="SCHEDULE",
            principal_type="GOAL",
            principal_id=goal.id,
            capabilities=frozenset({CAP_PROCESS_RUN}),
            trace_id="approval-flow",
            fencing_token="lease",
            fencing_owner_id="scheduler",
            fencing_subject_type="GOAL",
            fencing_subject_id=goal.id,
        )
        with runtime.database.get_connection() as connection:
            connection.execute(
                "INSERT INTO turns(id,conversation_id,ordinal,state,mode,started_at) "
                "VALUES(?,?,1,'RUNNING','auto',?)",
                (security.turn_id, conversation_id, time.time()),
            )
            connection.execute(
                "UPDATE goals SET state='RUNNING',lease_id='lease',lease_owner_id='scheduler',"
                "lease_expires_at=? WHERE id=?",
                (time.time() + 300, goal.id),
            )
        yield runtime, goal, security
    finally:
        runtime.close()


def model_process_call(runtime, security, args, operation="process.run"):
    responses = iter(
        [
            json.dumps(
                {
                    "action": "use_tool",
                    "tool": "kitt_runtime",
                    "tool_input": {"operation": operation, "arguments": args},
                    "content": None,
                    "reasoning_summary": "Run the workspace check.",
                    "loop": None,
                }
            ),
            json.dumps(
                {
                    "action": "final_response",
                    "tool": None,
                    "tool_input": None,
                    "content": "done",
                    "reasoning_summary": "Check complete.",
                    "loop": None,
                }
            ),
        ]
    )

    def stream(*_args, **_kwargs):
        yield next(responses), None

    command = TurnCommand(
        conversation_id=security.conversation_id,
        prompt="inspect workspace",
        mode="auto",
        turn_id=security.turn_id,
        security_context=security,
    )
    request = ExecutionRequest(
        system_prompt="Use the host tool.",
        messages=[{"role": "user", "content": command.prompt}],
        enabled_tools=["kitt_runtime"],
        agent_route="agent-loop",
    )
    with patch.object(runtime.processor, "_stream_execution_response", side_effect=stream):
        return [
            event
            for event, _, _ in runtime.processor._execute_tool_loop_core(
                command,
                request,
                ModelProfile(backend="openai", model="fixture"),
                None,
                runtime.workspace_id,
                security,
            )
        ]


def command_args():
    # Keep the actual executable spelling from the user log. sys.executable
    # resolves to python3.14 and missed the python3 -c policy branch before.
    return {
        "argv": [
            "python3",
            "-c",
            "with open('executed.txt','a') as output:\n    output.write('once\\n')",
        ],
        "timeout_seconds": 20,
        "network": False,
    }


def test_allow_all_model_process_does_not_request_approval(model_goal, tmp_path):
    runtime, _, security = model_goal
    runtime.policy.autonomy = AutonomyPolicy.preset("allow-all")
    events = model_process_call(runtime, security, command_args())
    assert not any(isinstance(event, (ApprovalRequired, TurnFailed)) for event in events), events
    assert (tmp_path / "executed.txt").read_text() == "once\n"


@pytest.mark.parametrize("changed_while_waiting", [False, True])
def test_composite_patch_preserves_approval_preconditions(
    model_goal, tmp_path, changed_while_waiting
):
    runtime, _, security = model_goal
    security = replace(security, capabilities=security.capabilities | {CAP_REPO_WRITE})
    path = tmp_path / "sample.txt"
    path.write_text("before\n")
    args = {"patch": "sample.txt\n<<<<<<< SEARCH\nbefore\n=======\nafter\n>>>>>>> REPLACE\n"}
    events = model_process_call(runtime, security, args, "patch.apply")
    approval = next(event for event in events if isinstance(event, ApprovalRequired))
    action = runtime.history.repo.get_valid_pending_action(
        f"pa_{security.turn_id}", security.workspace_id
    )
    assert action.tool_name == "apply_patch"
    assert action.affected_paths == ["sample.txt"]
    if changed_while_waiting:
        path.write_text("changed\n")
    grant = runtime.approval.issue_grant(
        security.turn_id,
        security.conversation_id,
        security.workspace_id,
        action.action_hash,
        approval_id=approval.approval_request_id,
    )
    results = list(runtime.processor.continue_turn(security.turn_id, grant))
    assert any(isinstance(event, TurnFailed) for event in results) == changed_while_waiting, results
    assert path.read_text() == ("changed\n" if changed_while_waiting else "after\n")


@pytest.mark.parametrize("remember_scope", [None, "session", "workspace"])
def test_model_pending_action_resumes_same_fenced_goal(model_goal, tmp_path, remember_scope):
    runtime, goal, security = model_goal
    runtime.policy.autonomy = AutonomyPolicy.preset("supervised")
    args = command_args()
    events = model_process_call(runtime, security, args)
    approval = next(event for event in events if isinstance(event, ApprovalRequired))
    action = runtime.history.repo.get_valid_pending_action(
        f"pa_{security.turn_id}", security.workspace_id
    )
    assert action.tool_name == "run_command"
    assert action.normalized_args == args
    assert action.action_hash == runtime.policy.generate_action_hash(action.tool_name, args)
    assert not (tmp_path / "executed.txt").exists()
    with runtime.database.get_connection() as connection:
        connection.execute(
            "UPDATE goals SET state='WAITING_APPROVAL',lease_id=NULL,lease_owner_id=NULL,"
            "lease_expires_at=NULL WHERE id=?",
            (goal.id,),
        )
    if remember_scope:
        runtime.approval.remember(
            "run_command",
            "**",
            "allow",
            remember_scope,
            conversation_id=security.conversation_id if remember_scope == "session" else None,
            workspace_id=security.workspace_id,
            executable_identity=f"GOAL:{goal.id}",
        )
    grant = runtime.approval.issue_grant(
        security.turn_id,
        security.conversation_id,
        security.workspace_id,
        action.action_hash,
        approval_id=approval.approval_request_id,
    )
    resolution = resolve_goal_approval(runtime, grant)
    assert resolution.success, resolution.error
    assert (tmp_path / "executed.txt").read_text() == "once\n"
    assert runtime.goals.get(goal.id).state == "ACTIVE"
    assert runtime.approval.is_nonce_used(grant.nonce)
    assert runtime.history.repo.get_valid_pending_action(action.id, action.workspace_id) is None
    assert not resolve_goal_approval(runtime, grant).success
    assert (tmp_path / "executed.txt").read_text() == "once\n"


@pytest.mark.asyncio
async def test_plain_ui_resumes_contract_through_its_owner(model_goal, tmp_path):
    runtime, goal, security = model_goal
    runtime.config = replace(runtime.config, daemon_enabled=False)
    events = model_process_call(runtime, security, command_args())
    approval = next(event for event in events if isinstance(event, ApprovalRequired))
    with runtime.database.get_connection() as connection:
        connection.execute(
            "UPDATE goals SET state='WAITING_APPROVAL',lease_id=NULL,lease_owner_id=NULL,"
            "lease_expires_at=NULL WHERE id=?",
            (goal.id,),
        )

    def observe(*_args):
        yield approval
        yield TurnCompleted(response="contract resumed")

    output = io.StringIO()
    ui = PlainLineUI(runtime, input_stream=io.StringIO("y\n"), output_stream=output)
    with (
        patch("kitt.goals.auto_contract.iter_automatic_contract", side_effect=observe),
        patch.object(
            runtime.history, "get_or_create_active", return_value={"id": security.conversation_id}
        ),
    ):
        await ui.run_turn("inspect workspace")
    assert runtime.goals.get(goal.id).state == "ACTIVE"
    assert "contract resumed" in output.getvalue()
    assert "Error:" not in output.getvalue()
    assert (tmp_path / "executed.txt").read_text() == "once\n"
