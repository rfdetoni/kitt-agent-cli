from types import SimpleNamespace
from unittest.mock import Mock, patch

from kitt.core.tool_approval import require_tool_approval
from kitt.core.turn_events import ApprovalRequired, TurnBlocked


def _scenario(*, cancelled=False, registration_error=None):
    manager = SimpleNamespace(
        register_request=Mock(side_effect=registration_error),
        deny=Mock(),
    )
    repository = SimpleNamespace(
        save_pending_action=Mock(), cancel_pending_action=Mock(),
    )
    processor = SimpleNamespace(
        root_path=".",
        history_service=SimpleNamespace(repo=repository),
        registry=SimpleNamespace(
            approval_manager=manager,
            policy=SimpleNamespace(
                generate_action_hash=lambda action, payload: "hash",
                autonomy=SimpleNamespace(),
            ),
            process_runner=SimpleNamespace(
                sandbox=SimpleNamespace(default_profile="workspace-write"),
            ),
        ),
        pending_actions={},
        turn_guard=SimpleNamespace(begin=Mock(return_value=True), end=Mock()),
        _args_digest=lambda args: "digest",
        _cancel_requested=lambda turn_id: cancelled,
    )
    cmd = SimpleNamespace(turn_id="turn-1", conversation_id="conv-1")
    security = SimpleNamespace(
        to_dict=lambda: {},
        principal_type="USER",
        principal_id="person",
    )
    return processor, cmd, security, manager, repository


def _register(processor, cmd, security):
    with patch("kitt.core.tool_approval.capture_preconditions", return_value=[]):
        with patch("kitt.core.tool_approval.capture_authority_snapshot", return_value={}):
            return require_tool_approval(
                processor, cmd, "workspace", "write_file",
                {"path": "a.py", "content": "hello"},
                SimpleNamespace(metadata={}), security,
            )


def test_cancelled_turn_retires_both_approval_and_pending_action():
    processor, cmd, security, manager, repository = _scenario(cancelled=True)
    assert _register(processor, cmd, security) is None
    manager.deny.assert_called_once()
    repository.cancel_pending_action.assert_called_once()
    assert processor.pending_actions == {}
    processor.turn_guard.end.assert_called_once_with(cmd.turn_id)


def test_decided_request_yields_block_without_another_modal():
    processor, cmd, security, manager, repository = _scenario(
        registration_error=ValueError("Approval request has already been decided")
    )
    result = _register(processor, cmd, security)
    assert isinstance(result, TurnBlocked)
    assert "already been decided" in result.reason
    repository.save_pending_action.assert_not_called()
    assert processor.pending_actions == {}
    processor.turn_guard.end.assert_called_once_with(cmd.turn_id)


def test_pending_request_is_durable_before_approval_modal():
    processor, cmd, security, manager, repository = _scenario()
    result = _register(processor, cmd, security)
    assert isinstance(result, ApprovalRequired)
    repository.save_pending_action.assert_called_once()
    assert cmd.turn_id in processor.pending_actions
    manager.deny.assert_not_called()
