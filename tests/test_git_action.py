"""Policy-governed Git actions must not silently escape host restrictions."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

from kitt.runtime.core_runtime import SafeRuntime, OPERATION_REGISTRY
from kitt.security.capabilities import CAP_PROCESS_RUN


def test_git_action_stays_in_policy_and_workspace_scope():
    assert OPERATION_REGISTRY["git.action"].required_capability == CAP_PROCESS_RUN
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "safe.py").write_text("x=1\n", encoding="utf-8")
        runtime = object.__new__(SafeRuntime)
        runtime.root = root
        runtime._op_registry_tool = Mock(return_value=SimpleNamespace(success=True))
        ctx = SimpleNamespace(is_path_scoped=False, assert_path_allowed=Mock())
        plan = runtime._op_git_action(
            {"action": "stage", "paths": ["safe.py"], "dry_run": True},
            "t", "MODEL", ctx,
        )
        assert plan.success and plan.data["argv"] == ["git", "add", "--", "safe.py"]
        runtime._op_registry_tool.assert_not_called()
        result = runtime._op_git_action(
            {"action": "stage", "paths": ["safe.py"]}, "t", "MODEL", ctx
        )
        assert result.success
        delegate = runtime._op_registry_tool.call_args
        assert delegate.args[0:2] == ("git.action", "run_command")
        assert delegate.args[2]["network"] is False
        assert delegate.args[2]["argv"] == plan.data["argv"]
        ctx.assert_path_allowed.assert_called_with("safe.py")

        for request in (
            {"action": "stage", "paths": ["../escape.py"]},
            {"action": "stage", "paths": [".git/config"]},
            {"action": "stage", "paths": ["C:/windows/file"]},
            {"action": "stage", "paths": ["safe.py"], "message": "oops"},
            {"action": "commit", "message": "bad\nmessage"},
            {"action": "push"},
            {"action": "status", "dry_run": "true"},
            {"action": "commit", "paths": ["safe.py"], "message": "bad"},
        ):
            assert not runtime._op_git_action(request, "t", "MODEL", ctx).success

        scoped = SimpleNamespace(is_path_scoped=True, assert_path_allowed=Mock())
        assert not runtime._op_git_action(
            {"action": "commit", "message": "something"}, "t", "MODEL", scoped
        ).success
