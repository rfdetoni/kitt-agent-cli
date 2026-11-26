from unittest.mock import Mock, patch

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import CAP_PROCESS_RUN, CAP_REPO_WRITE
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.registry import ToolRegistry


def test_remote_execution_requires_exact_approval_even_under_allow_all(tmp_path):
    registry = ToolRegistry(root_dir=str(tmp_path))
    registry.policy.autonomy = AutonomyPolicy.preset("allow-all")
    registry.policy.approval_manager = registry.approval_manager
    runtime = SafeRuntime(tmp_path, "ws", "conv", tool_registry=registry)
    context = ExecutionSecurityContext.create_user_context(
        workspace_id="ws", conversation_id="conv", turn_id="turn",
        capabilities={CAP_PROCESS_RUN, CAP_REPO_WRITE},
    )
    binding = {"runtime_id": "lab", "config_digest": "d", "created_at": 1}
    manager = Mock()
    manager.agent_status.return_value = {"enabled": True, "binding": binding}
    manager.execute.return_value = {"success": True, "stdout": "remote"}
    registry.conversation_runtimes = Mock()
    registry.conversation_runtimes.for_conversation.return_value = manager
    args = {"argv": ["true"], "binding": binding, "request_id": "one"}
    try:
        with patch.object(registry.policy, "evaluate_tool", return_value="ALLOW"):
            result = runtime.execute("computer.run", args, turn_id="turn", security_context=context)
            assert result.requires_approval
            manager.execute.assert_not_called()
            action_hash = registry.policy.generate_action_hash("run_command", args)
            registry.approval_manager.register_request("turn", "conv", "ws", action_hash,
                                                        "remote-approval", tool_name="run_command")
            grant = registry.approval_manager.issue_grant(
                "turn", "conv", "ws", action_hash, approval_id="remote-approval")
            approved = runtime.execute("computer.run", args, turn_id="turn", security_context=context,
                                       approval_grant=grant, expected_approval_id="remote-approval")
            assert approved.success, approved.error
            assert approved.metadata["local_workspace_verified"] is False
            assert manager.execute.call_args.kwargs["expected_agent_binding"] == binding
            repeated = runtime.execute("computer.run", args, turn_id="turn", security_context=context,
                                       approval_grant=grant, expected_approval_id="remote-approval")
            assert not repeated.success
            manager.execute.assert_called_once()
            manager.agent_status.return_value = {"enabled": True, "binding": {**binding, "created_at": 2}}
            changed = runtime.execute("computer.run", args, turn_id="turn", security_context=context)
            assert not changed.success and not changed.requires_approval
    finally:
        registry.close()
